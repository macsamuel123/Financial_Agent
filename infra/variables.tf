variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region for ECS Fargate deployment"
}

variable "container_image" {
  type        = string
  description = "URI of the Docker image stored in ECR"
}

variable "openai_api_key" {
  type        = string
  sensitive   = true
  description = "OpenAI API key for the LangGraph agent"
}

variable "database_url`" {
  type        = string
  sensitive   = true
  description = "Neon Postgres connection string"
}