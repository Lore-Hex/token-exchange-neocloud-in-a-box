# Bounded Lab Memory

The old 1536 GiB staging floor plus 768 GiB container cap excluded smaller
host-RAM offers even though this lab uses one fixed 510,311,045,295-byte
[DeepSeek pack](../../models/deepseek-v4.1-flash-1017/1/pack.yaml). This change
reduces the container limit to 512 GiB and requires 1152 GiB available host RAM:
the complete checkpoint plus the maximum container allocation plus 128 GiB
host reserve is less than that floor. This is explicit capacity arithmetic,
not a measured production working-set estimate.

Both Docker memory limits must equal 512 GiB (no additional swap). Inspection
rejects the previous larger cap, unlimited memory, lower/mismatched values and
swap expansion. Preparation still requires zero host swap and at least 600 GiB
free on the existing protected tmpfs. No mounts, swap settings, hardware
attestation policy, engine flags, deadlines, sampling or correctness tests
change. The 8 GiB pre-serve mode is unchanged.

Software tests cover exact staging boundaries, complete weight/cap/reserve
arithmetic and Docker limit mutations. A fresh committed-source hardware run
must pass before treating this lower-memory envelope as functional. It does
not establish B200 qualification, production capacity, benchmark quality,
confidential service identity or months-long availability.
