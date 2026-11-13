output "ecs_cluster_name" {
  value       = aws_ecs_cluster.cluster.name
  description = "The name of the ECS cluster"
}

output "vpc_id" {
  value = aws_vpc.main.id
}