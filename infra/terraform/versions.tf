# Terraform skeleton — ADR-010.
#
# ZERO RESOURCES BY DESIGN. This declares provider, version and region pinning
# only. Nothing here provisions anything and `apply` is never run: creating
# infrastructure incurs cost, which is a Class A decision
# requiring explicit approval.
#
# Region is pinned to ap-south-1 (Mumbai) to satisfy RBI payment-data
# localisation and Master Spec §41.3 data residency. That constraint is
# architectural, not a preference — see §4, which keeps the system outside
# payment-aggregator licensing by never touching funds.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Backend intentionally unconfigured. State holds resource metadata and can
  # hold secrets; wiring a remote backend is a Phase 17 decision.
  # backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "prayas"
      ManagedBy   = "terraform"
      DataClass   = "payments-india"
      Residency   = "in"
    }
  }
}
