#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Generate a nonfunctional GitHub-shaped token in temporary storage; never
# print the planted value or relax the scanner configuration to admit it.
printf 'access_token = "ghp_%s"\n' "$(openssl rand -hex 18)" > "$tmp/planted.txt"
status=0
"$root/build/gitleaks" dir --redact --no-banner --exit-code 42 "$tmp" > "$tmp/scan.log" 2>&1 || status=$?
if [[ "$status" != 42 ]]; then
  printf 'Secret scanner positive control failed (exit %s, expected 42).\n' "$status" >&2
  exit 1
fi
rm "$tmp/planted.txt" "$tmp/scan.log"
"$root/build/gitleaks" dir --redact --no-banner --exit-code 42 "$tmp"
printf 'Secret scanner rejected the planted token and accepted the cleaned directory.\n'

# A deleted key still leaks through Git history. Prove that mode independently.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
git -c init.defaultBranch=main init --quiet "$tmp/history"
git -C "$tmp/history" config user.email scanner@example.invalid
git -C "$tmp/history" config user.name 'Secret scanner positive control'
printf 'access_token = "ghp_%s"\n' "$(openssl rand -hex 18)" > "$tmp/history/planted.txt"
git -C "$tmp/history" add planted.txt
git -C "$tmp/history" -c commit.gpgsign=false commit --quiet -m 'Add nonfunctional positive control'
git -C "$tmp/history" rm --quiet planted.txt
git -C "$tmp/history" -c commit.gpgsign=false commit --quiet -m 'Remove positive control'
status=0
"$root/build/gitleaks" git --redact --no-banner --exit-code 42 --log-opts=--all "$tmp/history" > "$tmp/history-scan.log" 2>&1 || status=$?
if [[ "$status" != 42 ]]; then
  printf 'Git-history positive control failed (exit %s, expected 42).\n' "$status" >&2
  exit 1
fi
printf 'Secret scanner rejected a token retained only in Git history.\n'

# A known synthetic hash is exempt only at its exact evidence field and path.
# An unrelated value in the same field must still be detected.
# Random hex can contain default stopwords (for example "dead") or fall below
# the generic rule's entropy threshold. Hash a fixed public fixture instead.
unknown=$(printf %s nib-scanner-boundary-positive-control-v1 | openssl dgst -sha256 | awk '{print $NF}')
for receipt in tool-protocol-fail fixed-pass runtime-pass runtime-adapter-source runtime-parity-source cpu-readiness-source cpu-argv-source cpu-base-inspect-source provider-result provider-adapter-source provider-parity-source cpu-native-source cpu-native-tokens cpu-session-source cpu-session-pair-source stream-adapter-source stream-parity-source stream-inner-source; do
  evidence_path="evidence/p2/2026-10-22-tokenizer-parity-$receipt/output.log"
  field=tokens_sha256
  known=9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16
  suffix=''
  case "$receipt" in
    stream-adapter-source|stream-parity-source|stream-inner-source)
      evidence_path=evidence/p2/2026-10-23-stream-preparation-failure/input-contract.json
      field=tokenizer-parity.py
      known=09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a
      if [[ "$receipt" == stream-adapter-source ]]; then
        field=tokenizer-adapter.py
        known=060220d1bc62f13278e5a03a3a85c39ef84f528ada2a9e39ab0c62a4a7c09960
        suffix=,
      elif [[ "$receipt" == stream-inner-source ]]; then
        field=stream/engine_tokenizer_parity.py
        suffix=,
      fi
      ;;
    cpu-native-source|cpu-session-source|cpu-session-pair-source)
      evidence_path=evidence/p2/2026-10-22-cpu-parity-native-pass/output.log
      if [[ "$receipt" == cpu-session-source ]]; then
        evidence_path=evidence/p2/2026-10-22-cpu-session-parity-failure/output.log
      elif [[ "$receipt" == cpu-session-pair-source ]]; then
        evidence_path=evidence/p2/2026-10-22-cpu-session-parity-failure/pair/output.log
      fi
      field=scripts/engine_tokenizer_parity.py
      known=09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a
      suffix=,
      ;;
    cpu-native-tokens)
      evidence_path=evidence/p2/2026-10-22-cpu-parity-native-pass/output.log
      known=1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd
      ;;
    provider-result)
      evidence_path=evidence/p2/2026-10-22-provider-conformance-pass/output.log
      ;;
    provider-adapter-source)
      evidence_path=evidence/p2/2026-10-22-provider-conformance-pass/input-contract.json
      field=tokenizer-adapter.py
      known=060220d1bc62f13278e5a03a3a85c39ef84f528ada2a9e39ab0c62a4a7c09960
      suffix=,
      ;;
    provider-parity-source)
      evidence_path=evidence/p2/2026-10-22-provider-conformance-pass/input-contract.json
      field=tokenizer-parity.py
      known=09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a
      ;;
    runtime-adapter-source)
      evidence_path=evidence/p2/2026-10-22-tokenizer-parity-runtime-pass/input-contract.json
      field=tokenizer-adapter.py
      known=060220d1bc62f13278e5a03a3a85c39ef84f528ada2a9e39ab0c62a4a7c09960
      suffix=,
      ;;
    runtime-parity-source)
      evidence_path=evidence/p2/2026-10-22-tokenizer-parity-runtime-pass/input-contract.json
      field=tokenizer-parity.py
      known=09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a
      ;;
    cpu-readiness-source|cpu-argv-source|cpu-base-inspect-source)
      evidence_path="evidence/p2/2026-10-22-cpu-parity-${receipt#cpu-}"
      evidence_path="${evidence_path%-source}-failure/output.log"
      field=scripts/engine_tokenizer_parity.py
      known=09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a
      suffix=,
      ;;
  esac
  mkdir -p "$tmp/receipt/$(dirname "$evidence_path")"
  printf '            "%s": "%s"%s\n' "$field" "$known" "$suffix" > "$tmp/receipt/$evidence_path"
  "$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
    --exit-code 42 "$tmp/receipt" > "$tmp/receipt-scan.log" 2>&1
  printf '            "%s": "%s"%s\n' "$field" "$unknown" "$suffix" \
    > "$tmp/receipt/$evidence_path"
  status=0
  "$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
    --exit-code 42 "$tmp/receipt" > "$tmp/receipt-scan.log" 2>&1 || status=$?
  if [[ "$status" != 42 ]]; then
    printf 'Synthetic-hash exception escaped its exact value boundary (%s).\n' "$receipt" >&2
    exit 1
  fi
  printf 'Secret scanner still detects unknown values in the hash receipt field.\n'
  for boundary in path field; do
    target="$tmp/receipt/$evidence_path"
    name=$field
    if [[ "$boundary" == path ]]; then target="$tmp/receipt/other.log"; fi
    if [[ "$boundary" == field ]]; then name=api_key; fi
    printf '            "%s": "%s"%s\n' "$name" "$known" "$suffix" > "$target"
    status=0
    "$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
      --exit-code 42 "$target" > "$tmp/receipt-scan.log" 2>&1 || status=$?
    if [[ "$status" != 42 ]]; then
      printf 'Synthetic-hash exception escaped its %s boundary.\n' "$boundary" >&2
      exit 1
    fi
  done
  printf 'Synthetic-hash exception requires the exact field and path.\n'
  rm "$tmp/receipt/$evidence_path" "$tmp/receipt/other.log"
done

# New held-pair originals include compact JSON. Exempt only the matched field,
# never another candidate secret on the same line as a known public digest.
expect_receipt_scan() {
  local status=0
  "$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
    --exit-code 42 --report-format json --report-path "$tmp/held-report.json" "$1" \
    > "$tmp/held-scan.log" 2>&1 || status=$?
  if [[ "$status" != "$2" ]]; then
    printf 'Held-pair scanner boundary failed (%s, expected %s).\n' "$status" "$2" >&2
    exit 1
  fi
}
write_receipt_field() {
  if [[ "$format" == compact ]]; then
    printf '{"%s":"%s"}\n' "$2" "$3" > "$1"
  else
    printf '  "%s": "%s"%s\n' "$2" "$3" "$suffix" > "$1"
  fi
}

# This exception is a public source digest, not an operator-supplied value.
known=978814f3341b83fed33282f23ee76b173bc4235235ccf374149d0363252f3ce8
actual=$(openssl dgst -sha256 "$root/scripts/engine_tokenizer_parity.py" | awk '{print $NF}')
if [[ "$actual" != "$known" ]]; then
  printf 'Selected archive verifier source hash proof failed.\n' >&2
  exit 1
fi
target="$tmp/selected-archive/host/verify-selected-image-archive.py"
mkdir -p "$(dirname "$target")"
format=pretty
suffix=,
field=engine_tokenizer_parity
write_receipt_field "$target" "$field" "$known"
expect_receipt_scan "$target" 0
write_receipt_field "$target" "$field" "$unknown"
expect_receipt_scan "$target" 42
write_receipt_field "$target" api_key "$known"
expect_receipt_scan "$target" 42
write_receipt_field "$tmp/selected-archive/other.py" "$field" "$known"
expect_receipt_scan "$tmp/selected-archive/other.py" 42
printf '  "%s": "%s", "api_key": "%s"\n' "$field" "$known" "$unknown" > "$target"
expect_receipt_scan "$target" 42
python3 - "$tmp/held-report.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    findings = json.load(stream)
assert any(row["RuleID"] == "generic-api-key" and row["Match"].startswith("api_key") for row in findings)
PY
printf 'Selected archive source proof and exact value/field/path/same-line boundaries passed.\n'

# The source-derived prefix and controls are compact JSON; never exempt the
# entire line, which could also contain an unrelated secret candidate.
for trial_set in preflight-missing-source simulator-seed-failure retail-pass sequential-opened sequential-settled sequential-trial sequential-failed-opened sequential-failed-trial airline-pass-opened airline-pass-settled airline-pass-trial airline-reward-expectation-opened airline-reward-expectation-settled airline-reward-expectation-trial airline-socket-failure-opened airline-socket-failure-trial; do
  while IFS='|' read -r field known; do
    format=compact
    target="$tmp/external/evidence/p2/2026-10-26-tau-external-$trial_set/output.log"
    case "$trial_set" in
      sequential-opened|sequential-settled|sequential-trial)
        relative=event-000.json
        [[ "$trial_set" != sequential-settled ]] || relative="receipt-00.json"
        [[ "$trial_set" != sequential-trial ]] || relative=trial-00/output.log
        target="$tmp/external/evidence/p2/2026-10-27-sequential-tau-offline/journal/$relative"
        ;;
      sequential-failed-opened|sequential-failed-trial)
        relative=event-000.json
        [[ "$trial_set" != sequential-failed-trial ]] || relative=trial-00/output.log
        target="$tmp/external/evidence/p2/2026-10-27-sequential-tau-offline-socket-denied/journal/$relative"
        ;;
      airline-*)
        outcome=${trial_set#airline-}
        outcome=${outcome%-*}
        relative=event-000.json
        [[ "$trial_set" != *-settled ]] || relative="receipt-04.json"
        [[ "$trial_set" != *-trial ]] || relative=trial-04/output.log
        [[ "$trial_set" != airline-socket-failure-trial ]] || relative=trial-00/output.log
        target="$tmp/external/evidence/p2/2026-10-27-airline-offline-$outcome/journal/$relative"
        ;;
    esac
    mkdir -p "$(dirname "$target")"
    write_receipt_field "$target" "$field" "$known"
    expect_receipt_scan "$target" 0
    write_receipt_field "$target" "$field" "$unknown"
    expect_receipt_scan "$target" 42
    write_receipt_field "$target" api_key "$known"
    expect_receipt_scan "$target" 42
    write_receipt_field "$tmp/external/other.log" "$field" "$known"
    expect_receipt_scan "$tmp/external/other.log" 42
    if [[ "$trial_set" == airline-* ]]; then
      case "$trial_set" in
        *-settled) outside="receipt-05.json" ;;
        airline-socket-failure-trial) outside=trial-01/output.log ;;
        *-trial) outside=trial-05/output.log ;;
        *) outside=event-001.json ;;
      esac
      outside_target="$tmp/external/evidence/p2/2026-10-27-airline-offline-$outcome/journal/$outside"
      mkdir -p "$(dirname "$outside_target")"
      write_receipt_field "$outside_target" "$field" "$known"
      expect_receipt_scan "$outside_target" 42
    fi
    printf '{"%s":"%s","api_key":"%s"}\n' "$field" "$known" "$unknown" > "$target"
    expect_receipt_scan "$target" 42
  done <<'EXTERNAL_HASHES'
engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f
tau_external_tokenizer.py|cb8ac0b55bd5da6399f7be594d39f962893b788a08990e52608b34a09daacd73
EXTERNAL_HASHES
done

for prefix_receipt in 2026-10-23-native-prefix-count 2026-10-24-selected-prefix-count; do
for known in \
  9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16 \
  c7b86dad075d1d51e4bf9aec664d9ce392e5d04a63babe717ab9354ec0b56032 \
  6ddfa5e6dbf2f45b8efc1e2ebda65f1f79c39b1a4ec8a7fce8e77fc5a0add7d5 \
  4e5eb88296779609b1ef81a335894e3ef5c284a94a8ceb1607421414c43c9cbe \
  bd6e6807cddbb0f4844b130b9d8e7632f25ebc12a367ffb9ab412d1b23340c09; do
  format=compact
  target="$tmp/prefix/evidence/p2/$prefix_receipt/result/output.log"
  mkdir -p "$(dirname "$target")"
  write_receipt_field "$target" tokens_sha256 "$known"
  expect_receipt_scan "$target" 0
  write_receipt_field "$target" tokens_sha256 "$unknown"
  expect_receipt_scan "$target" 42
  write_receipt_field "$target" api_key "$known"
  expect_receipt_scan "$target" 42
  write_receipt_field "$tmp/prefix/other.log" tokens_sha256 "$known"
  expect_receipt_scan "$tmp/prefix/other.log" 42
  printf '{"tokens_sha256":"%s","api_key":"%s"}\n' "$known" "$unknown" > "$target"
  expect_receipt_scan "$target" 42
  python3 - "$tmp/held-report.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    findings = json.load(stream)
assert any(row["RuleID"] == "generic-api-key" and row["Match"].startswith("api_key") for row in findings)
PY
  printf 'Original prefix exact value/field/path and same-line secret boundaries passed.\n'
done
done

for receipt_set in cpu-session-native-pass tau-cpu-prompt-fit-pass tau-cpu-identity-followup tau-cpu-identity-17b; do
while IFS='|' read -r relative format field known suffix; do
  if [[ "$receipt_set" == tau-cpu-prompt-fit-pass ]]; then
    case "$field" in
      simulator-tokenizer.json) known=a51a69f903338d9651a6853f3f452bcb63bfad0a279eb721c17d6557f20165b4 ;;
      evaluator-tokenizer.json) known=dae972a868e9cd829062348c9bc4e51b1fc3d4b51dd18fc122818c2ae2e83b8c ;;
    esac
  fi
  target="$tmp/held/evidence/p2/2026-10-22-$receipt_set/$relative"
  if [[ "$receipt_set" == tau-cpu-identity-followup || "$receipt_set" == tau-cpu-identity-17b ]]; then
    target="$tmp/held/evidence/p2/2026-10-25-$receipt_set/$relative"
    case "$field" in
      scripts/engine_tokenizer_parity.py) known=6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f ;;
      simulator-tokenizer.json) known=d00605c53bcc2f82eaefcaa59b1ee1aaee43ce9698f0606441bbb3c5f7e74bd5 ;;
      evaluator-tokenizer.json) known=092cb8e872d01648096dc113b225e4cdca377794593fe899822ea88b2e73a1c6 ;;
    esac
  fi
  if [[ "$receipt_set" == tau-cpu-identity-17b ]]; then
    case "$field" in
      simulator-tokenizer.json) known=60f179bdd6dccdc4502cac6a7d7e244540077f3467d6659c747467518222ec76 ;;
      evaluator-tokenizer.json) known=1876eaef26f004664a5e0282c2f810d49cf232e3e2d788f0d17f8f80512cf797 ;;
    esac
  fi
  if [[ "$relative" == pair/scorer/output.log || "$field" == tokenizer_sha256 && "$format" == pretty ]]; then
    [[ "$receipt_set" == tau-cpu-identity-17b ]] || continue
  fi
  mkdir -p "$(dirname "$target")"
  write_receipt_field "$target" "$field" "$known"
  expect_receipt_scan "$target" 0
  write_receipt_field "$target" "$field" "$unknown"
  expect_receipt_scan "$target" 42
  write_receipt_field "$target" api_key "$known"
  expect_receipt_scan "$target" 42
  write_receipt_field "$tmp/held/other.log" "$field" "$known"
  expect_receipt_scan "$tmp/held/other.log" 42
  printf '{"%s":"%s","api_key":"%s"}\n' "$field" "$known" "$unknown" > "$target"
  expect_receipt_scan "$target" 42
  python3 - "$tmp/held-report.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    findings = json.load(stream)
assert any(row["RuleID"] == "generic-api-key" and row["Match"].startswith("api_key") for row in findings)
PY
  printf 'Held-pair exact value/field/path and same-line secret boundaries passed.\n'
done <<'CASES'
output.log|pretty|scripts/engine_tokenizer_parity.py|09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a|,
pair/output.log|pretty|scripts/engine_tokenizer_parity.py|09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a|,
pair/output.log|pretty|tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
output.log|pretty|simulator-tokenizer.json|de96abc426d3e94cfbc45736e27641c1498220325c57b08306d8eaa98728300f|
pair/output.log|pretty|simulator-tokenizer.json|de96abc426d3e94cfbc45736e27641c1498220325c57b08306d8eaa98728300f|
output.log|pretty|evaluator-tokenizer.json|132e5b388cb51278d647d7e1b9627754c5bf4d2537450a749b774314423e430d|,
pair/output.log|pretty|evaluator-tokenizer.json|132e5b388cb51278d647d7e1b9627754c5bf4d2537450a749b774314423e430d|,
pair/simulator-runtime.json|compact|tokenizer_sha256|554324f21418252a59edb045493a143a783b499d67b8680883e310e0f2ee77d9|
pair/simulator-tokenizer.json|compact|tokenizer_sha256|554324f21418252a59edb045493a143a783b499d67b8680883e310e0f2ee77d9|
pair/evaluator-runtime.json|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
pair/evaluator-tokenizer.json|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
pair/simulator-tokenizer.json|compact|endpoint_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
pair/evaluator-tokenizer.json|compact|endpoint_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
pair/simulator-tokenizer.json|compact|independent_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
pair/evaluator-tokenizer.json|compact|independent_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
output.log|pretty|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
pair/output.log|pretty|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
pair/scorer/output.log|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
CASES
done

# The all-refs scan also visits the image verifier's public preparation pin.
# Recompute it from immutable main ancestry; never exempt an arbitrary digest.
known=978814f3341b83fed33282f23ee76b173bc4235235ccf374149d0363252f3ce8
actual=$(GIT_NO_LAZY_FETCH=1 GIT_ALLOW_PROTOCOL='' git --no-replace-objects -C "$root" \
  show ef99d58bd1a2204ecac8d8de122f82e04904048f:scripts/engine_tokenizer_parity.py \
  | openssl dgst -sha256 | awk '{print $NF}')
if [[ "$actual" != "$known" ]]; then
  printf 'Image verifier public source hash proof failed.\n' >&2
  exit 1
fi
target="$tmp/image-archive/host/verify-selected-image-archive.py"
mkdir -p "$(dirname "$target")"
printf '    "engine_tokenizer_parity": "%s",\n' "$known" > "$target"
expect_receipt_scan "$target" 0
printf '    "engine_tokenizer_parity": "%s",\n' "$unknown" > "$target"
expect_receipt_scan "$target" 42
printf '    "api_key": "%s",\n' "$known" > "$target"
expect_receipt_scan "$target" 42
printf '    "engine_tokenizer_parity": "%s",\n' "$known" > "$tmp/image-archive/other.py"
expect_receipt_scan "$tmp/image-archive/other.py" 42
printf '"engine_tokenizer_parity": "%s", "api_key": "%s"\n' "$known" "$unknown" > "$target"
expect_receipt_scan "$target" 42
printf 'Image verifier source-hash exception requires exact value, field and path; adjacent secrets remain detected.\n'

# CPU retry originals require the exact observed source/token hashes.
while IFS='|' read -r relative field known suffix; do
  format=pretty
  target="$tmp/cpu-retry/evidence/p2/$relative"
  mkdir -p "$(dirname "$target")"
  write_receipt_field "$target" "$field" "$known"
  expect_receipt_scan "$target" 0
  write_receipt_field "$target" "$field" "$unknown"
  expect_receipt_scan "$target" 42
  write_receipt_field "$target" api_key "$known"
  expect_receipt_scan "$target" 42
  write_receipt_field "$tmp/cpu-retry/other.log" "$field" "$known"
  expect_receipt_scan "$tmp/cpu-retry/other.log" 42
  printf '"%s": "%s", "api_key": "%s"\n' "$field" "$known" "$unknown" > "$target"
  expect_receipt_scan "$target" 42
  printf 'CPU retry original hash exception requires exact value, field and path.\n'
done <<'CASES'
2026-10-24-selected-tau-readiness-failure-1800/cpu/output.log|scripts/engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f|,
2026-10-24-cpu-http-threads-pass/output.log|scripts/engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f|,
2026-10-24-cpu-http-threads-pass/output.log|tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
2026-10-24-cpu-http-threads-pass/output.log|tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
2026-10-24-cpu-http-threads-pass/output.log|tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
2026-10-24-cpu-http-threads-pass/output.log|tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
CASES

# Selected trial originals retain exact public hashes, not whole-line exemptions.
for trial_set in protocol-failure-1854 tool-error-2009 tool-rejection-0245 email-relation-0429 customer-resolution-1117 public-conformance; do
while IFS='|' read -r relative format field known suffix; do
  if [[ "$trial_set" == public-conformance && ( "$relative" != evidence/output.log || "$field" != scripts/engine_tokenizer_parity.py ) ]]; then
    continue
  fi
  if [[ "$field" == tokenizer_sha256 && "$relative" != cpu/* && "$trial_set" != customer-resolution-1117 ]]; then
    continue
  fi
  target="$tmp/selected-trial/evidence/p2/2026-10-24-selected-tau-$trial_set/$relative"
  if [[ "$trial_set" == tool-error-2009 ]]; then
    case "$field" in
      evaluator-tokenizer.json) known=07ed74d0563fd884342188b317a55e28bfa9a24563b3b705b16868dfd99e68e0 ;;
      simulator-tokenizer.json) known=db0c5e83d5b746a7f5e6c5f33a0e3d9e1cbb47c46961d8ac0a4a9bcc41437229 ;;
    esac
  fi
  if [[ "$trial_set" == tool-rejection-0245 ]]; then
    target="$tmp/selected-trial/evidence/p2/2026-10-25-selected-tau-$trial_set/$relative"
    case "$field" in
      evaluator-tokenizer.json) known=b18f504ad5436175a17b5a72258e698a9ad85a06c4d83bd32e20e47decb7e19f ;;
      simulator-tokenizer.json) known=7366286c4deaa828cebdbabbb25ae69e2a08751c87e76dcd9c7bfb5be8eb5d6e ;;
    esac
  fi
  if [[ "$trial_set" == email-relation-0429 ]]; then
    target="$tmp/selected-trial/evidence/p2/2026-10-25-selected-tau-$trial_set/$relative"
    case "$field" in
      evaluator-tokenizer.json) known=5399ca695c17ed4a152ae834f258daab23b5fc92a58b67124f86ffc7cb0f0393 ;;
      simulator-tokenizer.json) known=477d1e86f6fbd5083acaf2560e78abf4b014371c71cc750d68fd1edbaf0ca09a ;;
    esac
  fi
  if [[ "$trial_set" == customer-resolution-1117 ]]; then
    target="$tmp/selected-trial/evidence/p2/2026-10-25-selected-$trial_set/$relative"
    case "$field" in
      evaluator-tokenizer.json) known=f16cd5c46c9cdb24fccba8be1c07222f8632f42a3c2eb541de5b8f6f38bef79b ;;
      simulator-tokenizer.json) known=c712f03c74f4046360aaa5f36e74b15685e24329f9627ac3c3d679be896a765d ;;
    esac
  fi
  if [[ "$trial_set" == public-conformance ]]; then
    target="$tmp/selected-trial/evidence/p2/2026-10-25-selected-public-conformance/conformance.json"
  fi
  mkdir -p "$(dirname "$target")"
  write_receipt_field "$target" "$field" "$known"
  expect_receipt_scan "$target" 0
  write_receipt_field "$target" "$field" "$unknown"
  expect_receipt_scan "$target" 42
  write_receipt_field "$target" api_key "$known"
  expect_receipt_scan "$target" 42
  write_receipt_field "$tmp/selected-trial/other.log" "$field" "$known"
  expect_receipt_scan "$tmp/selected-trial/other.log" 42
  printf '{"%s":"%s","api_key":"%s"}\n' "$field" "$known" "$unknown" > "$target"
  expect_receipt_scan "$target" 42
  python3 - "$tmp/held-report.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    findings = json.load(stream)
assert any(row["RuleID"] == "generic-api-key" and row["Match"].startswith("api_key") for row in findings)
PY
  printf 'Selected trial exact value/field/path and same-line secret boundaries passed.\n'
done <<'CASES'
gpu/output.log|pretty|tokens_sha256|9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16|
gpu/output.log|pretty|tokens_sha256|c7b86dad075d1d51e4bf9aec664d9ce392e5d04a63babe717ab9354ec0b56032|
gpu/output.log|pretty|tokens_sha256|6ddfa5e6dbf2f45b8efc1e2ebda65f1f79c39b1a4ec8a7fce8e77fc5a0add7d5|
gpu/output.log|pretty|tokens_sha256|4e5eb88296779609b1ef81a335894e3ef5c284a94a8ceb1607421414c43c9cbe|
cpu/output.log|pretty|scripts/engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f|,
cpu/output.log|pretty|evaluator-tokenizer.json|25c731a878fa4f13f4e158b58c1784d2da90ed8c9115b2f68365fd3901213bd4|,
cpu/output.log|pretty|simulator-tokenizer.json|ac949d6497e9084feaa0dc6b3ce4e3f6e98aafda700431ad6e12708917fb3f38|
scorer/output.log|compact|scripts/engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f|
evidence/output.log|compact|scripts/engine_tokenizer_parity.py|6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f|
cpu/output.log|pretty|tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
cpu/output.log|pretty|tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
cpu/output.log|pretty|tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
cpu/output.log|pretty|tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
cpu/simulator-runtime.json|compact|tokenizer_sha256|554324f21418252a59edb045493a143a783b499d67b8680883e310e0f2ee77d9|
cpu/simulator-tokenizer.json|compact|tokenizer_sha256|554324f21418252a59edb045493a143a783b499d67b8680883e310e0f2ee77d9|
cpu/simulator-tokenizer.json|compact|endpoint_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
cpu/simulator-tokenizer.json|compact|endpoint_tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
cpu/simulator-tokenizer.json|compact|endpoint_tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
cpu/simulator-tokenizer.json|compact|endpoint_tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
cpu/simulator-tokenizer.json|compact|independent_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
cpu/simulator-tokenizer.json|compact|independent_tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
cpu/simulator-tokenizer.json|compact|independent_tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
cpu/simulator-tokenizer.json|compact|independent_tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
cpu/evaluator-runtime.json|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
cpu/evaluator-tokenizer.json|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
cpu/evaluator-tokenizer.json|compact|endpoint_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
cpu/evaluator-tokenizer.json|compact|endpoint_tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
cpu/evaluator-tokenizer.json|compact|endpoint_tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
cpu/evaluator-tokenizer.json|compact|endpoint_tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
cpu/evaluator-tokenizer.json|compact|independent_tokens_sha256|1b7419454da94e65624a0661ad74892eb52f65719dca41152acf9a6aa78b3cbd|
cpu/evaluator-tokenizer.json|compact|independent_tokens_sha256|fbaf9b9465ebf03803f0a8b5a24ca6b38dede64a287439371f87958033074fd4|
cpu/evaluator-tokenizer.json|compact|independent_tokens_sha256|c7450fed1a18f2dc5a4de67c84383999a8e240336ce4bd132c32e1cbf9b4ee9b|
cpu/evaluator-tokenizer.json|compact|independent_tokens_sha256|8da30159fe7f3f8c8d893edc5c34df0cfd719620133706bcf6e2c254c3a432fe|
scorer/output.log|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
evidence/output.log|compact|tokenizer_sha256|604363ffa22647728e0dd56e84926ad3ca1422999e356af99628fe2535b010a8|
CASES
done

# Stream originals get the same exact field/value/path boundaries.
for stream_set in window-exhaustion 600s-complete selected-timeout selected-provider selected-tau8k selected-graphs selected-c8 selected-batch32; do
while IFS='|' read -r relative field known suffix; do
  format=pretty
  target="$tmp/stream/evidence/p2/2026-10-23-stream-$stream_set/$relative"
  if [[ "$stream_set" == selected-timeout || "$stream_set" == selected-graphs ]]; then
    target="$tmp/stream/evidence/p2/2026-10-24-selected-stream-timeout/$relative"
    if [[ "$stream_set" == selected-graphs ]]; then
      target="$tmp/stream/evidence/p2/2026-10-24-selected-graphs-stream/$relative"
      [[ "$field" != tokenizer-parity.py ]] || suffix=,
    fi
    case "$field" in
      tokenizer-parity.py|stream/engine_tokenizer_parity.py)
        known=98872d2cb2e0b0874c79ef475c31db5a1d90b9024dc245f7cd0344b7173ed090 ;;
    esac
  fi
  if [[ "$stream_set" == selected-provider || "$stream_set" == selected-tau8k ]]; then
    target="$tmp/stream/evidence/p2/2026-10-24-selected-provider-conformance/$relative"
    if [[ "$stream_set" == selected-tau8k ]]; then
      target="$tmp/stream/evidence/p2/2026-10-24-selected-tau8k-parity/$relative"
      [[ "$field" != tokenizer-parity.py ]] || suffix=,
    fi
    case "$field" in
      stream/engine_tokenizer_parity.py|token_ids_sha256) continue ;;
      tokenizer-parity.py) known=6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f ;;
    esac
  fi
  if [[ "$stream_set" == selected-c8 || "$stream_set" == selected-batch32 ]]; then
    target="$tmp/stream/evidence/p2/2026-10-24-selected-c8-stream/$relative"
    if [[ "$stream_set" == selected-batch32 ]]; then
      target="$tmp/stream/evidence/p2/2026-10-26-selected-batch32/$relative"
    fi
    case "$field" in
      tokenizer-parity.py|stream/engine_tokenizer_parity.py)
        known=6d89db5050f2f3d4d40c334a1f137f97807ea931e13ceaa0b50f633351abab6f
        suffix=, ;;
    esac
  fi
  mkdir -p "$(dirname "$target")"
  write_receipt_field "$target" "$field" "$known"
  expect_receipt_scan "$target" 0
  write_receipt_field "$target" "$field" "$unknown"
  expect_receipt_scan "$target" 42
  write_receipt_field "$target" api_key "$known"
  expect_receipt_scan "$target" 42
  write_receipt_field "$tmp/stream/other.log" "$field" "$known"
  expect_receipt_scan "$tmp/stream/other.log" 42
  printf '"%s": "%s", "api_key": "%s"\n' "$field" "$known" "$unknown" > "$target"
  expect_receipt_scan "$target" 42
  printf 'Stream original hash exception requires exact value, field and path.\n'
done <<'CASES'
input-contract.json|tokenizer-adapter.py|060220d1bc62f13278e5a03a3a85c39ef84f528ada2a9e39ab0c62a4a7c09960|,
input-contract.json|tokenizer-parity.py|09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a|
input-contract.json|stream/engine_tokenizer_parity.py|09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a|,
output.log|tokens_sha256|9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16|
output.log|tokens_sha256|c7b86dad075d1d51e4bf9aec664d9ce392e5d04a63babe717ab9354ec0b56032|
output.log|tokens_sha256|6ddfa5e6dbf2f45b8efc1e2ebda65f1f79c39b1a4ec8a7fce8e77fc5a0add7d5|
output.log|tokens_sha256|4e5eb88296779609b1ef81a335894e3ef5c284a94a8ceb1607421414c43c9cbe|
output.log|token_ids_sha256|d061ea411012b683576ee86d57d07054066b842e936edb4e0ff7d89bd97c5470|
output.log|token_ids_sha256|4d7e4f06506e1e47b470b40a38ec147fef8016cae2675b4fc1d7ee5d941088c7|
output.log|token_ids_sha256|ff76892724504c2a7ee072064c89c029664b421b491f7232a3a1e264d9653a1d|
output.log|token_ids_sha256|4c53823667bac7092f3f1aacf02c6a0a5f8d11ba6b855af70267f484b8d7bc83|
output.log|token_ids_sha256|b750144a2ab856791ad30cd5f8490509ecbbebf7f06bfcd729873becd36248ef|
output.log|token_ids_sha256|66580e2260a1cb593df35123b62ccd5834a9ba0e2f7aac5901f0ad841b9f3d06|
output.log|token_ids_sha256|e1fdec586d6a4fcdb513c01c85c3ae9ca4f3841215515bfccfee06d00cab5a02|
output.log|token_ids_sha256|c2b7a5883e367117f9956115c67ce31de3c8ae610396b9e214ab72faeed28cf2|
output.log|token_ids_sha256|72cb95039dc41812098f6f9e6d40525acc5fa3c41ab9550fdb12f60ea2b0c713|
CASES
done
