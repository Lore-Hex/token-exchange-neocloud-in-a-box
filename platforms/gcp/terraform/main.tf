# SPDX-License-Identifier: Apache-2.0
locals {
  region = join("-", slice(split("-", var.zone), 0, 2))
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
  display_name = "Isolated non-confidential A4 lab; no IAM roles granted"
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
  machine_type   = "a4-highgpu-8g"
  can_ip_forward = false
  region         = local.region

  disk {
    boot         = true
    auto_delete  = true
    disk_type    = "hyperdisk-balanced"
    disk_size_gb = 100
    source_image = var.boot_image
  }

  # A4 includes eight B200 GPUs and 32 Local SSDs automatically. Do not add
  # guest_accelerator, SCRATCH disks, or unsupported Persistent Disk resources.
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
    purpose         = "nib-p2-lab"
    confidentiality = "none"
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
