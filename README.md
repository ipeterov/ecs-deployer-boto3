# ecs-deployer-boto3

Deploy applications to AWS ECS using boto3 and pydantic.

This library provides a thin, typed wrapper over the AWS ECS deployment API.
It is decoupled from how you discover or provision your infrastructure
(CloudFormation, CDK, Terraform, or by hand) — you describe your services as
pydantic models and the library handles registering task definitions, creating
or updating services, cleaning up old revisions, and monitoring deployments.

Supports both EC2 (with placement strategy / constraints) and Fargate (with
capacity provider strategy and `awsvpc` network configuration).

## Installation

```bash
pip install ecs-deployer-boto3
```

## Usage

```python
import boto3

from ecs_deployer_boto3 import (
    ApplicationUpdater,
    Container,
    DeploymentMonitor,
    Service,
    Task,
)

services = [
    Service(
        name='web',
        cluster='my-cluster',
        task_definition=Task(
            family='web',
            containers=[
                Container(
                    name='web',
                    image='123456789.dkr.ecr.us-east-1.amazonaws.com/web:latest',
                    port_mappings=[{'container_port': 8000}],
                ),
            ],
            cpu='256',
            memory='512',
            requires_compatibilities=['FARGATE'],
        ),
        launch_type='FARGATE',
        network_configuration={
            'subnets': ['subnet-abc'],
            'security_groups': ['sg-abc'],
            'assign_public_ip': 'ENABLED',
        },
    ),
]

session = boto3.Session()

updater = ApplicationUpdater(services, session)
updater.update_application(environment={'FOO': 'bar'})

monitor = DeploymentMonitor(services, session)
monitor.monitor(limit_minutes=15)
```

## License

MIT
