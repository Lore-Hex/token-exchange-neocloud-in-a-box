# SPDX-License-Identifier: Apache-2.0
variable "instance_count" {
  description = "0 removes this entire lab; 1 requests one billable A4 Spot VM. Apply requires separate approval."
  type        = number
  default     = 0
  nullable    = false
  validation {
    condition     = contains([0, 1], var.instance_count)
    error_message = "instance_count must be 0 or 1."
  }
}

variable "project_id" {
  description = "Existing, separately approved isolated lab project. This module never creates/enables a project or API."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id)) && var.project_id != "quill-cloud-proxy"
    error_message = "Use an explicit isolated lab project, never the production project."
  }
}

variable "name" {
  description = "Unique lab resource prefix within the project; not an existing deployment name."
  type        = string
  default     = "nib-a4-lab"
  nullable    = false
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,18}[a-z0-9]$", var.name))
    error_message = "name must be 6-20 lowercase letters, digits or hyphens, starting with a letter and ending alphanumeric."
  }
}

variable "zone" {
  description = "Explicit A4-capable zone; availability and quota must be checked separately. No discovery is performed."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", var.zone))
    error_message = "zone must be an explicit Compute Engine zone."
  }
}

variable "operator_cidr" {
  description = "Single explicit operator IPv4 address in /32 form; the only permitted inbound SSH source."
  type        = string
  nullable    = false
  validation {
    condition     = can(cidrnetmask(var.operator_cidr)) && can(regex("/32$", var.operator_cidr)) && var.operator_cidr != "0.0.0.0/32"
    error_message = "operator_cidr must be one explicit IPv4 /32, not a wildcard, IPv6 address or larger network."
  }
}

variable "boot_image" {
  description = "Reviewed exact Linux image URI projects/PROJECT/global/images/IMAGE; no family/latest lookup or default image."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^projects/[a-z][a-z0-9-]+/global/images/[a-z][a-z0-9-]+$", var.boot_image)) && !endswith(var.boot_image, "/latest")
    error_message = "boot_image must name an exact approved image, not a family or latest alias."
  }
}

variable "max_run_seconds" {
  description = "Explicit per-run maximum before automatic DELETE; still requires an independent operator teardown check."
  type        = number
  nullable    = false
  validation {
    condition     = var.max_run_seconds >= 60 && var.max_run_seconds <= 28800 && floor(var.max_run_seconds) == var.max_run_seconds
    error_message = "Choose an approved whole-second runtime between 60 seconds and eight hours."
  }
}
