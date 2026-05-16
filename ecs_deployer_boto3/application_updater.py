"""Deploy ECS services described by ``Service`` models."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from .models import Service
from .utils import chunker, echo


if TYPE_CHECKING:
    from boto3.session import Session
    from mypy_boto3_ecs import ECSClient


class ApplicationUpdater:
    """Reconcile a list of desired services against what's running in ECS.

    On ``update_application()``:

    - For each existing ECS service:
      - If it matches a desired service and can be updated in place, update it.
      - Otherwise, remove it (it was either renamed or removed from the config).
    - For each desired service that doesn't exist yet, create it.
    - Clean up any task definitions that are now INACTIVE.

    Some service properties can't be changed via ``update_service`` (placement
    strategy / constraints, deployment controller, role, launch type, capacity
    provider). When those change, the service is removed and re-created.
    """

    def __init__(
        self,
        services: list[Service],
        boto3_session: 'Session',
        dry_run: bool = False,
    ):
        self.ecs: 'ECSClient' = boto3_session.client('ecs')
        self.dry_run = dry_run

        if len({s.name for s in services}) != len(services):
            raise ValueError('Duplicate service name detected')

        self.desired_services = {s.name: s for s in services if s.desired_count}
        self.cluster_names = {s.cluster for s in services}

    # ----- helpers ---------------------------------------------------------

    def _call(
        self,
        method: Callable,
        params: dict,
        success_message: str | None = None,
    ) -> Any:
        """Call a boto3 method, or print what would be called in dry-run mode."""
        method_name = method.__name__

        if self.dry_run:
            # Avoid bloating the log with the full environment. The caller
            # already has access to the secrets, so this is about readability,
            # not security.
            params_for_log = params
            if method_name == 'register_task_definition':
                params_for_log = json.loads(json.dumps(params))
                for container in params_for_log.get('containerDefinitions', []):
                    container['environment'] = '[OMITTED FROM OUTPUT]'
            formatted = json.dumps(params_for_log, indent=2, sort_keys=True)
            echo(f'Would call `{method_name}` with:\n{formatted}')
            return None

        result = method(**params)
        if success_message:
            echo(success_message)
        return result

    @staticmethod
    def _explain_recreate(
        service_name: str,
        attribute: str,
        current: Any,
        desired: Any,
    ) -> None:
        echo(
            f'{service_name}: {attribute} forces re-creation, '
            f'current: {current!r}, desired: {desired!r}',
        )

    # ----- task definitions -----------------------------------------------

    def register_task(self, task_definition: dict) -> None:
        family = task_definition.get('family', '')
        echo(f'Registering task definition: {family}')

        response = self._call(self.ecs.register_task_definition, task_definition)
        if self.dry_run:
            return

        task_def = response['taskDefinition']
        revision = task_def['revision']
        echo(f'Revision registered: {family}:{revision}')

        if revision > 1:
            self.deregister_task_definition(f'{family}:{revision - 1}')

    def deregister_task_definition(self, task_definition: str) -> None:
        try:
            self.ecs.deregister_task_definition(taskDefinition=task_definition)
            echo(f'De-registered task definition {task_definition}')
        except ClientError as err:
            message = str(err)
            if 'in the process of being deleted' in message:
                echo(
                    f'Task definition {task_definition} is being deleted, skipping',
                )
            elif 'Unable to describe task definition' in message:
                echo(
                    f'Task definition {task_definition} already de-registered, skipping',
                )
            else:
                raise

    def delete_inactive_ecs_task_definitions(self) -> None:
        response = self.ecs.list_task_definitions(status='INACTIVE')
        inactive = response.get('taskDefinitionArns', [])
        echo(
            f'Cleaning up inactive ECS task definitions. Definitions to delete: {len(inactive)}',
        )
        if not inactive:
            return

        # AWS allows up to 10 deletions at a time.
        for batch in chunker(inactive, 10):
            self._call(
                self.ecs.delete_task_definitions,
                {'taskDefinitions': batch},
            )

    # ----- service reconciliation -----------------------------------------

    def get_existing_services(self, cluster: str):
        response = self.ecs.list_services(cluster=cluster)
        service_arns = response.get('serviceArns', [])
        if not service_arns:
            return
        for batch in chunker(service_arns, 10):
            response = self.ecs.describe_services(
                cluster=cluster,
                services=batch,
            )
            yield from response['services']

    def match_service_for_update(
        self,
        cluster_name: str,
        details: dict,
    ) -> Service | None:
        """Return the desired service if the existing one can be updated in
        place, otherwise None (caller will remove + re-create)."""
        service_name = details['serviceName']
        match = self.desired_services.get(service_name)

        if not match:
            echo(f'Service {service_name} not in desired services')
            return None

        if match.cluster != cluster_name:
            echo(f'Service {service_name} not in cluster {cluster_name}')
            return None

        # Task family
        task_arn = details['taskDefinition']
        current_family = task_arn.split('/')[-1].split(':')[0]
        if current_family != match.task_definition.family:
            self._explain_recreate(
                service_name,
                'task family',
                current_family,
                match.task_definition.family,
            )
            return None

        # Deployment controller
        current_controller = details['deploymentController']['type']
        if current_controller != match.deployment_controller:
            self._explain_recreate(
                service_name,
                'deployment controller',
                current_controller,
                match.deployment_controller,
            )
            return None

        # Placement strategy
        current_ps = details.get('placementStrategy', [])
        desired_ps = [p.as_aws_dict() for p in match.placement_strategy]
        if current_ps != desired_ps:
            self._explain_recreate(
                service_name,
                'placement strategy',
                current_ps,
                desired_ps,
            )
            return None

        # Placement constraints
        current_pc = details.get('placementConstraints', [])
        desired_pc = [p.as_aws_dict() for p in match.placement_constraints]
        if current_pc != desired_pc:
            self._explain_recreate(
                service_name,
                'placement constraints',
                current_pc,
                desired_pc,
            )
            return None

        # Service role (classic EC2 + ELB)
        current_role = details.get('roleArn')
        # AWS returns the role as an ARN; ours may be a name or an ARN. Match
        # by suffix to avoid spurious diffs.
        if match.role_arn and current_role:
            if not (
                current_role == match.role_arn
                or current_role.endswith(f'/{match.role_arn}')
                or match.role_arn.endswith(f'/{current_role}')
            ):
                self._explain_recreate(
                    service_name,
                    'role',
                    current_role,
                    match.role_arn,
                )
                return None
        elif (current_role is None) != (match.role_arn is None):
            self._explain_recreate(
                service_name,
                'role',
                current_role,
                match.role_arn,
            )
            return None

        # Launch type / capacity provider strategy. Switching between these
        # two requires a re-create.
        current_launch = details.get('launchType')
        current_cps = details.get('capacityProviderStrategy', [])
        desired_cps = [c.as_aws_dict() for c in match.capacity_provider_strategy]
        if bool(current_cps) != bool(desired_cps):
            self._explain_recreate(
                service_name,
                'capacity provider strategy presence',
                bool(current_cps),
                bool(desired_cps),
            )
            return None
        if not desired_cps and current_launch != match.launch_type:
            self._explain_recreate(
                service_name,
                'launch type',
                current_launch,
                match.launch_type,
            )
            return None

        return match

    def update_service(self, service_name: str, match: Service) -> None:
        echo(f'Updating service: {service_name}', prefix='\n->> ')

        if match.deployment_controller != 'ECS':
            raise ValueError(
                f'Unsupported deployment controller for in-place update: '
                f'{match.deployment_controller}',
            )

        self._call(
            self.ecs.update_service,
            match.as_aws_update_dict(),
            success_message=f'Service updated: {service_name}',
        )

    def remove_service(self, cluster_name: str, service_name: str) -> None:
        echo(f'Removing service: {service_name}')

        self._call(
            self.ecs.update_service,
            {'cluster': cluster_name, 'service': service_name, 'desiredCount': 0},
        )

        response = self._call(
            self.ecs.delete_service,
            {'cluster': cluster_name, 'service': service_name},
            success_message=f'Service removed: {service_name}',
        )
        if self.dry_run:
            return

        task_arn = response['service']['taskDefinition']
        self.deregister_task_definition(task_arn)

        echo(f'Waiting for service to terminate: {service_name}')
        waiter = self.ecs.get_waiter('services_inactive')
        waiter.wait(cluster=cluster_name, services=[service_name])

    def create_service(self, match: Service) -> None:
        echo(f'Creating service: {match.name}')
        self._call(
            self.ecs.create_service,
            match.as_aws_create_dict(),
            success_message=f'Service created: {match.name}',
        )

    # ----- orchestration --------------------------------------------------

    def _update_or_remove(
        self,
        environment: dict,
        cluster_name: str,
        ecs_details: dict,
    ) -> str | None:
        service_name = ecs_details['serviceName']
        match = self.match_service_for_update(cluster_name, ecs_details)
        if match is None:
            self.remove_service(cluster_name, service_name)
            return None

        self.register_task(match.task_definition.as_aws_dict(environment))
        self.update_service(service_name, match)
        return service_name

    def update_all_services(self, environment: dict) -> None:
        to_process = []
        for cluster_name in sorted(self.cluster_names):
            for ecs_details in self.get_existing_services(cluster_name):
                to_process.append((environment, cluster_name, ecs_details))
        to_process.sort(key=lambda x: x[2]['serviceName'])

        updated: list[str | None] = []
        if to_process:
            # Sequential in dry-run for readable output.
            threads = 1 if self.dry_run else len(to_process)
            with ThreadPoolExecutor(threads) as pool:
                updated = list(
                    pool.map(lambda args: self._update_or_remove(*args), to_process),
                )

        updated_names = {name for name in updated if name}

        for service_name, match in sorted(self.desired_services.items()):
            if service_name in updated_names:
                continue
            self.register_task(match.task_definition.as_aws_dict(environment))
            self.create_service(match)

    def update_application(self, environment: dict) -> None:
        """Reconcile ECS state to match the desired services."""
        if self.dry_run:
            echo('Running in dry run mode')

        self.update_all_services(environment)
        self.delete_inactive_ecs_task_definitions()
