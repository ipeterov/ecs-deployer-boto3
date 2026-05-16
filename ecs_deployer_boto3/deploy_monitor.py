"""Monitor an ECS deployment until it stabilizes."""

import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .models import Service
from .utils import chunker, echo


if TYPE_CHECKING:
    from boto3.session import Session
    from mypy_boto3_ecs import ECSClient
    from mypy_boto3_ecs.type_defs import TaskTypeDef


@dataclass
class ServiceStatus:
    """Runtime status of a service during deployment monitoring."""

    name: str
    desired_count: int
    actual_running_count: int = 0
    old_running_count: int = 0


class DeploymentMonitor:
    """Poll ECS until all services have reached their desired state.

    Compares each running task's task-definition revision against the latest
    revision for that family. A task at the latest revision counts as "new";
    anything older is an "old" task that should still drain. Stopped tasks
    that aren't the result of scaling activity are treated as failures
    (subject to ``deploy_monitoring_health_check_failed_count``).
    """

    def __init__(
        self,
        services: list[Service],
        boto3_session: 'Session',
        ignored_task_groups: Iterable[str] = (),
    ):
        self.ecs: 'ECSClient' = boto3_session.client('ecs')
        self.services = services
        self.cluster_names = {s.cluster for s in services}
        self.ignored_task_groups = frozenset(ignored_task_groups)

    def get_all_ecs_tasks(self, cluster: str) -> list['TaskTypeDef']:
        task_arns: list[str] = []
        for status in ['RUNNING', 'STOPPED']:
            paginator = self.ecs.get_paginator('list_tasks')
            for page in paginator.paginate(cluster=cluster, desiredStatus=status):
                task_arns.extend(page['taskArns'])

        if not task_arns:
            return []

        tasks: list[TaskTypeDef] = []
        for batch in chunker(task_arns, 100):
            response = self.ecs.describe_tasks(cluster=cluster, tasks=batch)
            tasks.extend(response['tasks'])
        return tasks

    def fetch_latest_task_definitions(self) -> dict[str, tuple[str, Service]]:
        """For each desired service, return the latest task-definition revision.

        Returns:
            ``{family: (latest_revision_str, service)}``
        """
        desired = {s.name: s for s in self.services}

        def fetch(service: Service) -> tuple[str, tuple[str, Service]]:
            response = self.ecs.list_task_definitions(
                familyPrefix=service.task_definition.family,
                maxResults=1,
                sort='DESC',
            )
            arn = response['taskDefinitionArns'][0]
            family_rev = arn.split('/')[-1]
            family, revision = family_rev.rsplit(':', 1)
            return family, (revision, service)

        with ThreadPoolExecutor(8) as pool:
            results = pool.map(fetch, desired.values())
        return dict(results)

    def get_ecs_status(self) -> dict:
        desired_services = {s.name: s for s in self.services}
        task_definitions_info = self.fetch_latest_task_definitions()

        running_new: dict[str, int] = defaultdict(int)
        running_old: dict[str, int] = defaultdict(int)
        stopped_tasks: list[TaskTypeDef] = []

        for cluster_name in self.cluster_names:
            for task in self.get_all_ecs_tasks(cluster_name):
                family_rev = task['taskDefinitionArn'].split('/')[-1]
                family, version = family_rev.rsplit(':', 1)
                if family not in task_definitions_info:
                    continue
                if task.get('group') in self.ignored_task_groups:
                    continue

                latest_version, service = task_definitions_info[family]
                status = task['lastStatus']
                stopped_reason = task.get('stoppedReason', '')

                if status == 'RUNNING':
                    if latest_version == version:
                        running_new[service.name] += 1
                    else:
                        running_old[service.name] += 1
                elif status == 'STOPPED':
                    if (
                        'Scaling activity initiated' not in stopped_reason
                        and latest_version == version
                    ):
                        stopped_tasks.append(task)

        statuses = [
            ServiceStatus(
                name=service.name,
                desired_count=service.desired_count,
                actual_running_count=running_new.get(service.name, 0),
                old_running_count=running_old.get(service.name, 0),
            )
            for service in desired_services.values()
        ]
        return {'services': statuses, 'stopped_tasks': stopped_tasks}

    def monitor(self, limit_minutes: int = 15) -> None:
        """Block until all services stabilize, raise on failure or timeout."""
        max_tries = 300
        start = datetime.now()
        notified_done: set[str] = set()
        failed_health_checks = {
            s.name: {
                'max': s.deploy_monitoring_health_check_failed_count,
                'count': 0,
            }
            for s in self.services
            if s.deploy_monitoring_health_check_failed_count is not None
        }

        for i in range(1, max_tries + 1):
            elapsed_seconds = (datetime.now() - start).seconds
            if elapsed_seconds > 60 * limit_minutes:
                raise TimeoutError(
                    f'Deployment not stable after {limit_minutes} minutes',
                )

            echo(f'[{datetime.now().isoformat()}] Try {i}...')
            status = self.get_ecs_status()

            stopped_tasks = [
                t for t in status['stopped_tasks'] if t['stoppingAt'].replace(tzinfo=None) > start
            ]

            if stopped_tasks:
                stop_monitoring = False
                for task in stopped_tasks:
                    stopped_reason = task.get('stoppedReason', '')
                    is_elb_error = 'Task failed ELB health checks' in stopped_reason
                    service_name = task['group'].removeprefix('service:')

                    if service_name in failed_health_checks and is_elb_error:
                        checks = failed_health_checks[service_name]
                        checks['count'] += 1
                        if checks['count'] > checks['max']:
                            self._describe_failed_task(task)
                            stop_monitoring = True
                        else:
                            self._describe_failed_task(task, 'WARNING')
                    else:
                        self._describe_failed_task(task)
                        stop_monitoring = True

                if stop_monitoring:
                    raise RuntimeError('Tasks stopped unexpectedly')

            incomplete = []
            for s in status['services']:
                done = s.desired_count == s.actual_running_count and not s.old_running_count
                if not done:
                    incomplete.append(s)
                elif s.name not in notified_done:
                    echo(
                        f'[DONE] Service: {s.name}, '
                        f'desired count: {s.desired_count}, '
                        f'running count: {s.actual_running_count}, '
                        f'old running count: {s.old_running_count}',
                    )
                    notified_done.add(s.name)

            if not incomplete:
                break

            for s in incomplete:
                echo(
                    f'[IN PROGRESS] Service: {s.name}, '
                    f'desired count: {s.desired_count}, '
                    f'actual running count: {s.actual_running_count}, '
                    f'old running count: {s.old_running_count}',
                )
            time.sleep(10)
        else:
            raise TimeoutError(f'Deployment not stable after {max_tries} tries')

        echo('Done')

    @staticmethod
    def _describe_failed_task(
        task: 'TaskTypeDef',
        severity: str = 'FAILED',
    ) -> None:
        region = task['taskArn'].split(':')[3]
        cluster = task['clusterArn'].split('/')[-1]
        task_id = task['taskArn'].split('/')[-1]

        domain = 'console.aws.amazon.com'
        if 'us-gov' in region:
            domain = 'console.amazonaws-us-gov.com'

        container = task['containers'][0] if task.get('containers') else {}
        echo(
            f'[{severity}] {task["taskDefinitionArn"]}, '
            f'stopped reason: {task.get("stoppedReason")}, '
            f'exit code: {container.get("exitCode")}, '
            f'status reason: {container.get("reason")}',
        )
        echo(
            f'More info at: '
            f'https://{domain}/ecs/home?'
            f'region={region}#/clusters/{cluster}/tasks/{task_id}/details\n',
        )
