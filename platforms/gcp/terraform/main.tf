# SPDX-License-Identifier: Apache-2.0
locals {
  region  = join("-", slice(split("-", var.zone), 0, 2))
  h100_td = var.hardware_profile == "a3-h100-tdx"
}

resource "google_compute_network" "lab" {
  count                   = var.instance_count
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "lab" {
  count         = var.instance_count
  name          = "${var.name}-subnet"
  network       = google_compute_network.lab[0].id
  region        = local.region
  ip_cidr_range = "10.77.0.0/24"
  stack_type    = "IPV4_ONLY"
}

resource "google_service_account" "lab" {
  count        = var.instance_count
  account_id   = "${var.name}-vm"
  display_name = local.h100_td ? "Isolated H100 TDX lab; no IAM roles granted" : "Isolated non-confidential A4 lab; no IAM roles granted"
}

# Fresh custom VPC: no default internal/SSH rules, no 443 allow, no IPv6.
# Higher-level organization/network policies still need a pre-run audit.
resource "google_compute_firewall" "ssh" {
  count                   = var.instance_count
  name                    = "${var.name}-operator-ssh"
  network                 = google_compute_network.lab[0].id
  direction               = "INGRESS"
  priority                = 1000
  source_ranges           = [var.operator_cidr]
  target_service_accounts = [google_service_account.lab[0].email]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# Google A4 guide, Create VPC networks / Spot:
# https://docs.cloud.google.com/compute/docs/gpus/create-gpu-vm-a3u-a4
# One GVNIC is explicitly supported for a quick test; no cluster/RDMA claim.
resource "google_compute_instance_template" "lab" {
  count          = var.instance_count
  name_prefix    = "${var.name}-"
  machine_type   = local.h100_td ? "a3-highgpu-1g" : "a4-highgpu-8g"
  can_ip_forward = false
  region         = local.region

  disk {
    boot         = true
    auto_delete  = true
    disk_type    = local.h100_td ? "pd-balanced" : "hyperdisk-balanced"
    interface    = local.h100_td ? "NVME" : null
    disk_size_gb = 100
    source_image = var.boot_image
  }

  # A4 includes GPUs/local SSD automatically; A3 includes its H100. TDX only
  # supports NVMe Balanced PD, not Local SSD on A3. Do not add accelerators.
  # https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations
  dynamic "confidential_instance_config" {
    for_each = local.h100_td ? [true] : []
    content {
      confidential_instance_type = "TDX"
    }
  }

  dynamic "shielded_instance_config" {
    for_each = local.h100_td ? [true] : []
    content {
      enable_secure_boot          = true
      enable_vtpm                 = true
      enable_integrity_monitoring = true
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.lab[0].id
    nic_type   = "GVNIC"
    stack_type = "IPV4_ONLY"
    access_config {
      network_tier = "PREMIUM"
    }
  }

  service_account {
    email  = google_service_account.lab[0].email
    scopes = []
  }

  scheduling {
    provisioning_model          = "SPOT"
    preemptible                 = true
    automatic_restart           = false
    on_host_maintenance         = "TERMINATE"
    instance_termination_action = "DELETE"
    max_run_duration {
      seconds = var.max_run_seconds
    }
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    serial-port-enable     = "FALSE"
  }

  labels = {
    purpose         = local.h100_td ? "nib-p3-lab" : "nib-p2-lab"
    confidentiality = local.h100_td ? "unverified-tdx" : "none"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_instance_from_template" "lab" {
  count                    = var.instance_count
  name                     = var.name
  zone                     = var.zone
  source_instance_template = google_compute_instance_template.lab[0].self_link_unique
  deletion_protection      = false
  depends_on               = [google_compute_firewall.ssh]
}
