# SPDX-License-Identifier: Apache-2.0
terraform {
  required_version = "= 1.14.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 8.7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = local.region
  zone    = var.zone
}
