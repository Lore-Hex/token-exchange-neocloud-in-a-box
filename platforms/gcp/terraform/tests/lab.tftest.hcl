# SPDX-License-Identifier: Apache-2.0
# Every run is plan-only with a mocked provider. These are NOT cloud plans.
mock_provider "google" {
  override_during = plan
  mock_resource "google_compute_network" {
    defaults = { id = "projects/nib-synthetic-lab/global/networks/nib-a4-lab-vpc" }
  }
  mock_resource "google_compute_subnetwork" {
    defaults = { id = "projects/nib-synthetic-lab/regions/us-central1/subnetworks/nib-a4-lab-subnet" }
  }
  mock_resource "google_service_account" {
    defaults = { email = "nib-a4-lab-vm@nib-synthetic-lab.iam.gserviceaccount.com" }
  }
  mock_resource "google_compute_instance_template" {
    defaults = { self_link_unique = "https://www.googleapis.com/compute/v1/projects/nib-synthetic-lab/global/instanceTemplates/synthetic-template?uniqueId=1" }
  }
}

variables {
  project_id      = "nib-synthetic-lab"
  zone            = "us-central1-a"
  operator_cidr   = "192.0.2.1/32"
  boot_image      = "projects/nib-synthetic-images/global/images/fixture-not-a-real-image"
  max_run_seconds = 3600
}

run "default_zero" {
  command = plan
  assert {
    condition = alltrue([
      length(google_compute_network.lab) == 0,
      length(google_compute_subnetwork.lab) == 0,
      length(google_compute_firewall.ssh) == 0,
      length(google_service_account.lab) == 0,
      length(google_compute_instance_template.lab) == 0,
      length(google_compute_instance_from_template.lab) == 0,
      output.instance_name == null,
      output.service_account_email == null,
    ])
    error_message = "Default count must request no resources at all."
  }
}

run "one_spot_lab" {
  command = plan
  variables { instance_count = 1 }
  assert {
    condition = alltrue([
      length(google_compute_network.lab) == 1,
      length(google_compute_subnetwork.lab) == 1,
      length(google_compute_firewall.ssh) == 1,
      length(google_service_account.lab) == 1,
      length(google_compute_instance_template.lab) == 1,
      length(google_compute_instance_from_template.lab) == 1,
      !google_compute_network.lab[0].auto_create_subnetworks,
      google_compute_subnetwork.lab[0].network == google_compute_network.lab[0].id,
      google_compute_firewall.ssh[0].network == google_compute_network.lab[0].id,
      google_compute_firewall.ssh[0].direction == "INGRESS",
      google_compute_firewall.ssh[0].source_ranges == toset([var.operator_cidr]),
      google_compute_firewall.ssh[0].target_service_accounts == toset([google_service_account.lab[0].email]),
      one(google_compute_firewall.ssh[0].allow).protocol == "tcp",
      one(google_compute_firewall.ssh[0].allow).ports == tolist(["22"]),
    ])
    error_message = "Exactly one isolated lab with only operator SSH may be planned."
  }
  assert {
    condition = alltrue([
      google_compute_instance_template.lab[0].machine_type == "a4-highgpu-8g",
      !google_compute_instance_template.lab[0].can_ip_forward,
      length(google_compute_instance_template.lab[0].disk) == 1,
      one(google_compute_instance_template.lab[0].disk).disk_type == "hyperdisk-balanced",
      one(google_compute_instance_template.lab[0].disk).boot,
      one(google_compute_instance_template.lab[0].disk).auto_delete,
      one(google_compute_instance_template.lab[0].disk).source_image == var.boot_image,
      one(google_compute_instance_template.lab[0].disk).disk_size_gb == 100,
      length(google_compute_instance_template.lab[0].guest_accelerator) == 0,
      length(google_compute_instance_template.lab[0].confidential_instance_config) == 0,
      length(google_compute_instance_template.lab[0].shielded_instance_config) == 0,
      one(google_compute_instance_template.lab[0].network_interface).nic_type == "GVNIC",
      one(google_compute_instance_template.lab[0].network_interface).stack_type == "IPV4_ONLY",
      one(google_compute_instance_template.lab[0].network_interface).subnetwork == google_compute_subnetwork.lab[0].id,
      one(one(google_compute_instance_template.lab[0].network_interface).access_config).network_tier == "PREMIUM",
      one(google_compute_instance_template.lab[0].service_account).email == google_service_account.lab[0].email,
      length(one(google_compute_instance_template.lab[0].service_account).scopes) == 0,
      google_compute_instance_from_template.lab[0].source_instance_template == google_compute_instance_template.lab[0].self_link_unique,
    ])
    error_message = "The fixed A4/disk/NIC/template/service identity must remain bound."
  }
  assert {
    condition = alltrue([
      one(google_compute_instance_template.lab[0].scheduling).provisioning_model == "SPOT",
      one(google_compute_instance_template.lab[0].scheduling).preemptible,
      !one(google_compute_instance_template.lab[0].scheduling).automatic_restart,
      one(google_compute_instance_template.lab[0].scheduling).on_host_maintenance == "TERMINATE",
      one(google_compute_instance_template.lab[0].scheduling).instance_termination_action == "DELETE",
      one(one(google_compute_instance_template.lab[0].scheduling).max_run_duration).seconds == var.max_run_seconds,
      google_compute_instance_template.lab[0].metadata["enable-oslogin"] == "TRUE",
      google_compute_instance_template.lab[0].metadata["block-project-ssh-keys"] == "TRUE",
      google_compute_instance_template.lab[0].metadata["serial-port-enable"] == "FALSE",
      !google_compute_instance_from_template.lab[0].deletion_protection,
    ])
    error_message = "No standard provisioning, automatic restart, unbounded run, inherited SSH keys or deletion block."
  }
}

run "h100_default_zero" {
  command = plan
  variables { hardware_profile = "a3-h100-tdx" }
  assert {
    condition = alltrue([
      length(google_compute_network.lab) == 0,
      length(google_compute_subnetwork.lab) == 0,
      length(google_compute_firewall.ssh) == 0,
      length(google_service_account.lab) == 0,
      length(google_compute_instance_template.lab) == 0,
      length(google_compute_instance_from_template.lab) == 0,
    ])
    error_message = "Selecting H100 must not opt into any resources."
  }
}

run "one_h100_tdx_spot_lab" {
  command = plan
  variables {
    instance_count   = 1
    hardware_profile = "a3-h100-tdx"
    name             = "nib-h100-lab"
  }
  assert {
    condition = alltrue([
      google_compute_instance_template.lab[0].machine_type == "a3-highgpu-1g",
      one(google_compute_instance_template.lab[0].confidential_instance_config).confidential_instance_type == "TDX",
      one(google_compute_instance_template.lab[0].shielded_instance_config).enable_secure_boot,
      one(google_compute_instance_template.lab[0].shielded_instance_config).enable_vtpm,
      one(google_compute_instance_template.lab[0].shielded_instance_config).enable_integrity_monitoring,
      length(google_compute_instance_template.lab[0].disk) == 1,
      one(google_compute_instance_template.lab[0].disk).disk_type == "pd-balanced",
      one(google_compute_instance_template.lab[0].disk).interface == "NVME",
      one(google_compute_instance_template.lab[0].disk).auto_delete,
      one(google_compute_instance_template.lab[0].disk).source_image == var.boot_image,
      length(google_compute_instance_template.lab[0].guest_accelerator) == 0,
      google_compute_instance_template.lab[0].labels["confidentiality"] == "unverified-tdx",
      google_compute_instance_template.lab[0].labels["purpose"] == "nib-p3-lab",
    ])
    error_message = "H100 must use TDX, Secure Boot and NVMe Balanced PD without claiming attestation."
  }
  assert {
    condition = alltrue([
      !google_compute_network.lab[0].auto_create_subnetworks,
      one(google_compute_firewall.ssh[0].allow).ports == tolist(["22"]),
      google_compute_firewall.ssh[0].source_ranges == toset([var.operator_cidr]),
      google_compute_firewall.ssh[0].target_service_accounts == toset([google_service_account.lab[0].email]),
      length(one(google_compute_instance_template.lab[0].service_account).scopes) == 0,
      !google_compute_instance_template.lab[0].can_ip_forward,
      one(google_compute_instance_template.lab[0].scheduling).provisioning_model == "SPOT",
      !one(google_compute_instance_template.lab[0].scheduling).automatic_restart,
      one(google_compute_instance_template.lab[0].scheduling).on_host_maintenance == "TERMINATE",
      one(google_compute_instance_template.lab[0].scheduling).instance_termination_action == "DELETE",
      one(one(google_compute_instance_template.lab[0].scheduling).max_run_duration).seconds == var.max_run_seconds,
      google_compute_instance_template.lab[0].metadata["enable-oslogin"] == "TRUE",
      google_compute_instance_template.lab[0].metadata["block-project-ssh-keys"] == "TRUE",
      google_compute_instance_template.lab[0].metadata["serial-port-enable"] == "FALSE",
    ])
    error_message = "H100 must retain isolation, credential, SSH and finite-lifetime controls."
  }
}

run "h100_europe_zone" {
  command = plan
  variables {
    hardware_profile = "a3-h100-tdx"
    zone             = "europe-west4-c"
  }
}

run "h100_east_zone" {
  command = plan
  variables {
    hardware_profile = "a3-h100-tdx"
    zone             = "us-east5-a"
  }
}

run "reject_h100_other_zone" {
  command = plan
  variables {
    hardware_profile = "a3-h100-tdx"
    zone             = "us-central1-b"
  }
  expect_failures = [var.zone]
}

run "reject_unknown_hardware" {
  command = plan
  variables { hardware_profile = "a4-b200-tdx" }
  expect_failures = [var.hardware_profile]
}

run "explicit_zero" {
  command = plan
  variables { instance_count = 0 }
  assert {
    condition     = length(google_compute_instance_from_template.lab) == 0
    error_message = "Explicit zero must not request an instance."
  }
}

run "reject_two" {
  command = plan
  variables { instance_count = 2 }
  expect_failures = [var.instance_count]
}

run "reject_fraction" {
  command = plan
  variables { instance_count = 0.5 }
  expect_failures = [var.instance_count]
}

run "reject_public_ssh" {
  command = plan
  variables { operator_cidr = "0.0.0.0/0" }
  expect_failures = [var.operator_cidr]
}

run "reject_subnet_ssh" {
  command = plan
  variables { operator_cidr = "192.0.2.0/24" }
  expect_failures = [var.operator_cidr]
}

run "reject_ipv6" {
  command = plan
  variables { operator_cidr = "2001:db8::1/128" }
  expect_failures = [var.operator_cidr]
}

run "reject_family" {
  command = plan
  variables { boot_image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64" }
  expect_failures = [var.boot_image]
}

run "reject_production" {
  command = plan
  variables { project_id = "quill-cloud-proxy" }
  expect_failures = [var.project_id]
}

run "reject_unbounded_runtime" {
  command = plan
  variables { max_run_seconds = 0 }
  expect_failures = [var.max_run_seconds]
}
