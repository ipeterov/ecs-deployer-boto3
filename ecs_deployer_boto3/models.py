"""Pydantic models that mirror the subset of the AWS ECS API used for
deployment.

The general shape follows AWS's own API:
- Field names are snake_case versions of AWS's camelCase.
- Each model has an ``as_aws_dict()`` method that converts it back to the
  camelCase shape boto3 expects.
- Optional fields are only emitted when set, to match AWS's "omit means
  default" semantics.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# -----------------------------------------------------------------------------
# Task definition
# -----------------------------------------------------------------------------


class PortMapping(BaseModel):
    container_port: int = Field(..., ge=1, le=65535)
    host_port: int | None = Field(None, ge=0, le=65535)
    protocol: Literal['tcp', 'udp'] = 'tcp'

    def as_aws_dict(self) -> dict:
        result: dict = {
            'containerPort': self.container_port,
            'protocol': self.protocol,
        }
        if self.host_port is not None:
            result['hostPort'] = self.host_port
        return result


class HealthCheck(BaseModel):
    command: list[str] = Field(..., min_length=1)
    interval: int = Field(30, ge=5, le=300)
    timeout: int = Field(5, ge=2, le=60)
    retries: int = Field(3, ge=1, le=10)
    start_period: int = Field(0, ge=0, le=300)

    def as_aws_dict(self) -> dict:
        return {
            'command': self.command,
            'interval': self.interval,
            'timeout': self.timeout,
            'retries': self.retries,
            'startPeriod': self.start_period,
        }


class LogConfiguration(BaseModel):
    log_driver: str
    options: dict[str, str] = Field(default_factory=dict)

    def as_aws_dict(self) -> dict:
        result: dict = {'logDriver': self.log_driver}
        if self.options:
            result['options'] = self.options
        return result


class MountPoint(BaseModel):
    container_path: str
    source_volume: str
    read_only: bool = False

    def as_aws_dict(self) -> dict:
        return {
            'containerPath': self.container_path,
            'sourceVolume': self.source_volume,
            'readOnly': self.read_only,
        }


class VolumeFrom(BaseModel):
    source_container: str
    read_only: bool = False

    def as_aws_dict(self) -> dict:
        return {
            'sourceContainer': self.source_container,
            'readOnly': self.read_only,
        }


class HostVolumeProperties(BaseModel):
    source_path: str | None = None

    def as_aws_dict(self) -> dict:
        result: dict = {}
        if self.source_path is not None:
            result['sourcePath'] = self.source_path
        return result


class Volume(BaseModel):
    name: str
    host: HostVolumeProperties | None = None

    def as_aws_dict(self) -> dict:
        result: dict = {'name': self.name}
        if self.host is not None:
            result['host'] = self.host.as_aws_dict()
        return result


class LinuxCapabilities(BaseModel):
    add: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)

    def as_aws_dict(self) -> dict:
        result: dict = {}
        if self.add:
            result['add'] = self.add
        if self.drop:
            result['drop'] = self.drop
        return result


class LinuxParameters(BaseModel):
    capabilities: LinuxCapabilities | None = None
    init_process_enabled: bool = False

    def as_aws_dict(self) -> dict:
        result: dict = {}
        if self.capabilities is not None:
            result['capabilities'] = self.capabilities.as_aws_dict()
        if self.init_process_enabled:
            result['initProcessEnabled'] = self.init_process_enabled
        return result


class ContainerDependency(BaseModel):
    container_name: str
    condition: Literal['START', 'COMPLETE', 'SUCCESS', 'HEALTHY'] = 'START'

    def as_aws_dict(self) -> dict:
        return {
            'containerName': self.container_name,
            'condition': self.condition,
        }


class Secret(BaseModel):
    """A secret pulled from SSM or Secrets Manager at task start."""

    name: str
    value_from: str

    def as_aws_dict(self) -> dict:
        return {'name': self.name, 'valueFrom': self.value_from}


class Container(BaseModel):
    name: str
    image: str
    cpu: int | None = Field(None, ge=0)
    memory: int | None = Field(None, ge=4)
    memory_reservation: int | None = Field(None, ge=4)
    essential: bool = True
    command: list[str] | None = None
    entry_point: list[str] | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    secrets: list[Secret] = Field(default_factory=list)
    port_mappings: list[PortMapping] = Field(default_factory=list)
    mount_points: list[MountPoint] = Field(default_factory=list)
    volumes_from: list[VolumeFrom] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    hostname: str | None = None
    health_check: HealthCheck | None = None
    linux_parameters: LinuxParameters | None = None
    log_configuration: LogConfiguration | None = None
    depends_on: list[ContainerDependency] = Field(default_factory=list)
    stop_timeout: int | None = Field(None, ge=2, le=120)

    def as_aws_dict(self, environment: dict[str, str] | None = None) -> dict:
        """Convert to AWS container definition shape.

        Args:
            environment: Extra environment variables to merge in. Keys in the
                container's own ``environment`` take precedence over these.
        """
        merged_env = {**(environment or {}), **self.environment}

        result: dict = {
            'name': self.name,
            'image': self.image,
            'essential': self.essential,
            'environment': [{'name': k, 'value': str(v)} for k, v in merged_env.items()],
            'portMappings': [p.as_aws_dict() for p in self.port_mappings],
            'mountPoints': [m.as_aws_dict() for m in self.mount_points],
        }

        if self.cpu is not None:
            result['cpu'] = self.cpu
        if self.memory is not None:
            result['memory'] = self.memory
        if self.memory_reservation is not None:
            result['memoryReservation'] = self.memory_reservation
        if self.command is not None:
            result['command'] = self.command
        if self.entry_point is not None:
            result['entryPoint'] = self.entry_point
        if self.secrets:
            result['secrets'] = [s.as_aws_dict() for s in self.secrets]
        if self.volumes_from:
            result['volumesFrom'] = [v.as_aws_dict() for v in self.volumes_from]
        if self.links:
            result['links'] = self.links
        if self.hostname is not None:
            result['hostname'] = self.hostname
        if self.health_check is not None:
            result['healthCheck'] = self.health_check.as_aws_dict()
        if self.linux_parameters is not None:
            result['linuxParameters'] = self.linux_parameters.as_aws_dict()
        if self.log_configuration is not None:
            result['logConfiguration'] = self.log_configuration.as_aws_dict()
        if self.depends_on:
            result['dependsOn'] = [d.as_aws_dict() for d in self.depends_on]
        if self.stop_timeout is not None:
            result['stopTimeout'] = self.stop_timeout

        return result


class RuntimePlatform(BaseModel):
    """Runtime platform (OS + CPU architecture) for the task. Mainly used to
    target Fargate Graviton (ARM64)."""

    operating_system_family: Literal[
        'LINUX',
        'WINDOWS_SERVER_2019_FULL',
        'WINDOWS_SERVER_2019_CORE',
        'WINDOWS_SERVER_2022_FULL',
        'WINDOWS_SERVER_2022_CORE',
    ] = 'LINUX'
    cpu_architecture: Literal['X86_64', 'ARM64'] = 'X86_64'

    def as_aws_dict(self) -> dict:
        return {
            'operatingSystemFamily': self.operating_system_family,
            'cpuArchitecture': self.cpu_architecture,
        }


class Task(BaseModel):
    """An ECS task definition. Supports both EC2 and Fargate.

    For Fargate, set ``cpu`` and ``memory`` at the task level and use
    ``network_mode='awsvpc'``. For EC2 with bridge networking, leave ``cpu``
    and ``memory`` unset at the task level and set them per-container.
    """

    family: str
    containers: list[Container] = Field(..., min_length=1)
    cpu: str | None = None
    memory: str | None = None
    network_mode: Literal['awsvpc', 'bridge', 'host', 'none'] | None = None
    requires_compatibilities: list[Literal['EC2', 'FARGATE']] = Field(
        default_factory=list,
    )
    volumes: list[Volume] = Field(default_factory=list)
    task_role_arn: str | None = None
    execution_role_arn: str | None = None
    runtime_platform: RuntimePlatform | None = None

    def as_aws_dict(self, environment: dict[str, str] | None = None) -> dict:
        result: dict = {
            'family': self.family,
            'containerDefinitions': [c.as_aws_dict(environment) for c in self.containers],
        }

        if self.cpu is not None:
            result['cpu'] = self.cpu
        if self.memory is not None:
            result['memory'] = self.memory
        if self.network_mode is not None:
            result['networkMode'] = self.network_mode
        if self.requires_compatibilities:
            result['requiresCompatibilities'] = self.requires_compatibilities
        if self.volumes:
            result['volumes'] = [v.as_aws_dict() for v in self.volumes]
        if self.task_role_arn is not None:
            result['taskRoleArn'] = self.task_role_arn
        if self.execution_role_arn is not None:
            result['executionRoleArn'] = self.execution_role_arn
        if self.runtime_platform is not None:
            result['runtimePlatform'] = self.runtime_platform.as_aws_dict()

        return result


# -----------------------------------------------------------------------------
# Service (placement / capacity / network)
# -----------------------------------------------------------------------------


class PlacementStrategy(BaseModel):
    """EC2 placement strategy (e.g. spread tasks across instances)."""

    type: Literal['spread', 'binpack', 'random']
    field: str | None = None

    def as_aws_dict(self) -> dict:
        result: dict = {'type': self.type}
        if self.field is not None:
            result['field'] = self.field
        return result


class PlacementConstraint(BaseModel):
    """EC2 placement constraint."""

    type: Literal['distinctInstance', 'memberOf']
    expression: str | None = None

    def as_aws_dict(self) -> dict:
        result: dict = {'type': self.type}
        if self.expression is not None:
            result['expression'] = self.expression
        return result


class CapacityProviderStrategyItem(BaseModel):
    """One entry of a capacity provider strategy (e.g. FARGATE / FARGATE_SPOT)."""

    capacity_provider: str
    weight: int = Field(1, ge=0, le=1000)
    base: int = Field(0, ge=0, le=100000)

    def as_aws_dict(self) -> dict:
        result: dict = {
            'capacityProvider': self.capacity_provider,
            'weight': self.weight,
        }
        if self.base > 0:
            result['base'] = self.base
        return result


class AwsVpcConfiguration(BaseModel):
    subnets: list[str] = Field(..., min_length=1)
    security_groups: list[str] = Field(default_factory=list)
    assign_public_ip: Literal['ENABLED', 'DISABLED'] = 'DISABLED'

    def as_aws_dict(self) -> dict:
        result: dict = {
            'subnets': self.subnets,
            'assignPublicIp': self.assign_public_ip,
        }
        if self.security_groups:
            result['securityGroups'] = self.security_groups
        return result


class NetworkConfiguration(BaseModel):
    """``awsvpc`` networking config. Required for Fargate."""

    awsvpc_configuration: AwsVpcConfiguration

    def as_aws_dict(self) -> dict:
        return {'awsvpcConfiguration': self.awsvpc_configuration.as_aws_dict()}


# -----------------------------------------------------------------------------
# Deployment / load balancer
# -----------------------------------------------------------------------------


class DeploymentConfiguration(BaseModel):
    strategy: Literal['ROLLING', 'BLUE_GREEN'] = 'ROLLING'
    minimum_healthy_percent: int = Field(100, ge=0, le=200)
    maximum_percent: int = Field(200, ge=100, le=200)
    bake_time_in_minutes: int = Field(0, ge=0)

    @model_validator(mode='after')
    def validate_percentages(self) -> 'DeploymentConfiguration':
        if self.minimum_healthy_percent >= self.maximum_percent:
            raise ValueError(
                f'minimum_healthy_percent ({self.minimum_healthy_percent}) '
                f'must be less than maximum_percent ({self.maximum_percent})',
            )
        return self

    def as_aws_dict(self) -> dict:
        result: dict = {
            'strategy': self.strategy,
            'maximumPercent': self.maximum_percent,
            'minimumHealthyPercent': self.minimum_healthy_percent,
        }
        if self.strategy == 'BLUE_GREEN':
            result['bakeTimeInMinutes'] = self.bake_time_in_minutes
        return result


class BlueGreenConfiguration(BaseModel):
    """Advanced load balancer config for ECS blue/green deployments."""

    alternate_target_group_arn: str
    production_listener_rule: str
    role_arn: str

    def as_aws_dict(self) -> dict:
        return {
            'alternateTargetGroupArn': self.alternate_target_group_arn,
            'productionListenerRule': self.production_listener_rule,
            'roleArn': self.role_arn,
        }


class LoadBalancer(BaseModel):
    target_group_arn: str
    container_name: str
    container_port: int
    advanced_configuration: BlueGreenConfiguration | None = None

    def as_aws_dict(self) -> dict:
        result: dict = {
            'targetGroupArn': self.target_group_arn,
            'containerName': self.container_name,
            'containerPort': self.container_port,
        }
        if self.advanced_configuration is not None:
            result['advancedConfiguration'] = self.advanced_configuration.as_aws_dict()
        return result


# -----------------------------------------------------------------------------
# Service
# -----------------------------------------------------------------------------


class Service(BaseModel):
    """An ECS service. Supports both EC2 and Fargate launch types.

    Choose exactly one of ``launch_type`` or ``capacity_provider_strategy``.

    ``role_arn`` is the ECS service IAM role and is only used by classic EC2
    services with a load balancer (not Fargate, not awsvpc).

    ``network_configuration`` is required for ``awsvpc`` (Fargate or EC2 with
    awsvpc network mode).
    """

    name: str
    cluster: str
    task_definition: Task
    desired_count: int = Field(1, ge=0)
    deployment_controller: Literal['ECS', 'CODE_DEPLOY', 'EXTERNAL'] = 'ECS'
    deployment_configuration: DeploymentConfiguration = Field(
        default_factory=DeploymentConfiguration,
    )
    load_balancers: list[LoadBalancer] = Field(default_factory=list)
    health_check_grace_period_seconds: int | None = Field(None, ge=0)
    enable_execute_command: bool = False

    # Launch configuration (mutually exclusive)
    launch_type: Literal['EC2', 'FARGATE', 'EXTERNAL'] | None = None
    capacity_provider_strategy: list[CapacityProviderStrategyItem] = Field(
        default_factory=list,
    )

    # awsvpc networking (required for Fargate)
    network_configuration: NetworkConfiguration | None = None

    # EC2 placement
    placement_strategy: list[PlacementStrategy] = Field(default_factory=list)
    placement_constraints: list[PlacementConstraint] = Field(default_factory=list)

    # Classic ELB-with-EC2 service role
    role_arn: str | None = None

    # Forces a new deployment on update even when no service params changed.
    # Defaults to True since this library is meant for CI: an update_application
    # call should always roll the service. Also required by AWS when switching
    # to/from a capacity provider strategy.
    force_new_deployment: bool = True

    # Library-specific (not an AWS field). Tolerates this many ELB health-check
    # failures during deployment before bailing out. Useful for flaky checks.
    deploy_monitoring_health_check_failed_count: int | None = Field(
        None,
        ge=0,
    )

    @model_validator(mode='after')
    def validate_launch_config(self) -> 'Service':
        if self.launch_type and self.capacity_provider_strategy:
            raise ValueError(
                'launch_type and capacity_provider_strategy are mutually exclusive',
            )
        return self

    @model_validator(mode='after')
    def validate_role_arn(self) -> 'Service':
        if self.role_arn and not self.load_balancers:
            raise ValueError(
                'role_arn can only be set together with load_balancers',
            )
        return self

    @model_validator(mode='after')
    def validate_blue_green(self) -> 'Service':
        cfg = self.deployment_configuration
        if cfg.strategy == 'BLUE_GREEN':
            if not self.load_balancers:
                raise ValueError(
                    'load_balancers is required for BLUE_GREEN deployments',
                )
            if self.load_balancers[0].advanced_configuration is None:
                raise ValueError(
                    'load_balancers[0].advanced_configuration is required '
                    'for BLUE_GREEN deployments',
                )
        return self

    def _common_params(self) -> dict:
        params: dict = {
            'cluster': self.cluster,
            'taskDefinition': self.task_definition.family,
            'desiredCount': self.desired_count,
            'deploymentConfiguration': self.deployment_configuration.as_aws_dict(),
        }
        if self.enable_execute_command:
            params['enableExecuteCommand'] = True
        if self.launch_type is not None:
            params['launchType'] = self.launch_type
        if self.capacity_provider_strategy:
            params['capacityProviderStrategy'] = [
                item.as_aws_dict() for item in self.capacity_provider_strategy
            ]
        if self.network_configuration is not None:
            params['networkConfiguration'] = self.network_configuration.as_aws_dict()
        if self.load_balancers:
            params['loadBalancers'] = [lb.as_aws_dict() for lb in self.load_balancers]
        if self.health_check_grace_period_seconds is not None:
            params['healthCheckGracePeriodSeconds'] = self.health_check_grace_period_seconds
        return params

    def as_aws_create_dict(self) -> dict:
        """Parameters for boto3 ``ecs.create_service``."""
        params = self._common_params()
        params['serviceName'] = self.name
        params['deploymentController'] = {'type': self.deployment_controller}
        if self.placement_strategy:
            params['placementStrategy'] = [p.as_aws_dict() for p in self.placement_strategy]
        if self.placement_constraints:
            params['placementConstraints'] = [p.as_aws_dict() for p in self.placement_constraints]
        if self.role_arn is not None:
            params['role'] = self.role_arn
        return params

    def as_aws_update_dict(self) -> dict:
        """Parameters for boto3 ``ecs.update_service``."""
        params = self._common_params()
        params['service'] = self.name
        if self.force_new_deployment:
            params['forceNewDeployment'] = True
        # Placement strategy / constraints and role can't be changed via
        # update_service — they force a re-create (see ApplicationUpdater).
        return params
