# A4 Terraform Mock Plans

**Software-only fake-provider evidence, not a real cloud plan.** Produced on
2026-10-22 by source commit `538f97c54f6e649f9d8af29e270713f838f1794f`:

```sh
uv run --project tools --frozen python scripts/check_gcp_terraform.py \
  --evidence evidence/p2/2026-10-22-gcp-a4-mock-plan
```

The directory must not already exist. Use a new output directory to reproduce;
never overwrite these receipts. `run.json` records a portable command template;
the exact invocation above and each Terraform subcommand in `output.log`
describe this run. No cloud credentials, Terraform state or operator inputs
were inherited. No apply (including mock apply) or GCP call was made.

Original generated files:

- [output.log](output.log): format check, checksum-locked provider installation,
  schema validation, actual Terraform-rendered mock plans and test events.
- [tests.jsonl](tests.jsonl): all 11 plan cases passed, no failures/skips.
- [sha256.json](sha256.json): executed source and output-byte bindings.
- [run.json](run.json): producer/source/time metadata, execution `fake`,
  `hardware_validated: false`.

Default/explicit zero proposed no resources. Count one proposed six resources:
custom VPC, subnet, SSH firewall, role-free service account, A4 Spot template
and one instance using that template. Unsafe counts, broad/IPv6 SSH sources,
an image-family URI, the prohibited production project and zero runtime were
rejected as expected. Test IDs and computed provider values are synthetic.
Terraform's boilerplate references to "real infrastructure" or "after apply"
do not turn these mock plans into cloud observations.

These results do not prove cloud API acceptance, account permissions, billing,
quota, zone capacity, image compatibility, template inheritance at the API,
automatic disk attachment, SSH reachability, boot, model readiness, teardown
or cost. See the [skeleton's limits and sources](../../../platforms/gcp/terraform/README.md).
G-GCP-PROJECT, G-GCP-QUOTA and per-run G-RUN remain pending for a real deployment.
