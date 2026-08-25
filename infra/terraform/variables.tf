variable "region" {
  description = "AWS region. Must be an Indian region — §41.3 data residency."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = can(regex("^ap-south-", var.region))
    error_message = "Region must be Indian (ap-south-*). Payment data may not leave India."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}
