provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  ssm_prefix = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/financial-agent"
} 

# 1. Networking (VPC & Subnets for ECS Fargate)
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "financial-agent-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true

  tags = { Name = "financial-agent-public-subnet" }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
}

resource "aws_route_table_association" "rta" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "ecs_sg" {
  name        = "financial-agent-ecs-sg"
  description = "Allow outbound traffic for DB and API calls"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 2. IAM Roles for ECS Execution
resource "aws_iam_role" "ecs_execution_role" {
  name = "financial_agent_ecs_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_read_ssm" {
  name = "financial-agent-read-ssm"
  role = aws_iam_role.ecs_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = "${local.ssm_prefix}/*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/financial-agent"
  retention_in_days = 7
}

# 3. ECS Cluster & Fargate Task Definition
resource "aws_ecs_cluster" "cluster" {
  name = "financial-agent-cluster"
}

resource "aws_ecs_task_definition" "task" {
  family                   = "financial-agent-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn

  container_definitions = jsonencode([
    {
      name      = "agent-mcp"
      image     = var.container_image
      essential = true
            secrets = [
        { name = "OPENAI_API_KEY",   valueFrom = "${local.ssm_prefix}/OPENAI_API_KEY" },
        { name = "DEEPSEEK_API_KEY", valueFrom = "${local.ssm_prefix}/DEEPSEEK_API_KEY" },
        { name = "DATABASE_URL",     valueFrom = "${local.ssm_prefix}/DATABASE_URL" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "agent"
        }
      }
    }
  ])
}

terraform {
  backend "s3" {
    bucket       = "financial-agent-tfstate-fms"
    key          = "financial-agent/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}