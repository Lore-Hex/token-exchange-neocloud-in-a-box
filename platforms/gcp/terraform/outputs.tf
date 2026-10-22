# SPDX-License-Identifier: Apache-2.0
output "instance_name" {
  description = "Requested instance name, or null when disabled; not an availability or readiness proof."
  value       = one(google_compute_instance_from_template.lab[*].name)
}

output "service_account_email" {
  description = "Dedicated identity with no role grants from this module."
  value       = one(google_service_account.lab[*].email)
}
