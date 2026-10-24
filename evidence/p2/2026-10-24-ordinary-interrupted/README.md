# Interrupted Ordinary Baseline

The original receipts from producer
`323ab0eedacee474e5b12fcc80a169ef43400075` are preserved byte for byte.
The attempt ran from 01:05:32 to 01:16:42 UTC on October 24 and failed while
decoding an empty container result (`output_bytes=0`). It records
`cleanup=unconfirmed`, not a completed or cleaned-up inference run.

The last container observation says `running`, `exit_code=0` and
`oom_killed=false`. These observations do not establish successful inference,
clean shutdown, or the cause of interruption. No detailed hardware or
correctness result was received. The selected checkpoint identity and image
binding remain recorded; functional, deployable and confidential flags remain
false. Later recovery or successful attempts must not rewrite this result.
