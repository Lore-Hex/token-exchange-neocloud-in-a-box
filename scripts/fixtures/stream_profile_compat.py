# SPDX-License-Identifier: Apache-2.0
"""Exact opt-in normalization for historical-source regression assertions.

Only these literal, reviewed 1M additions may differ. Everything left over is
still compared byte-for-byte (or by the existing salt-only AST test) against
the immutable historical source. Runtime tests independently exercise both
branches, including their ceilings and malformed streams.
"""


def legacy_source(raw, module):
    if module == "stream_measurement":
        changes = (
            ('MILLION_PROFILE = "long1m"\n', ''),
            ('def stream_limit(profile=None):\n'
             '    require(profile is None or type(profile) is str and profile == MILLION_PROFILE)\n'
             '    return 512 << 20 if profile == MILLION_PROFILE else MAX_STREAM\n\n\n', ''),
            ('max_output_tokens, profile=None):', 'max_output_tokens):'),
            ('        self.max_stream_bytes = stream_limit(profile)\n'
             '        million = profile == MILLION_PROFILE\n', ''),
            ('(3540 if million else 1800)', '1800'),
            ('393216 if million else 131072', '131072'),
            ('self.bytes + len(data) > self.max_stream_bytes', 'self.bytes + len(data) > MAX_STREAM'),
        )
    elif module == "stream_transport":
        changes = (
            ('    def __init__(self, *args, profile=None, **kwargs):\n'
             '        self.max_stream_bytes = measurement.stream_limit(profile)\n'
             '        super().__init__(*args, **kwargs)\n\n', ''),
            ('require(size <= self.max_stream_bytes)', 'require(size <= measurement.MAX_STREAM)'),
            ('def response_length(response, *, profile=None):\n'
             '    maximum = measurement.stream_limit(profile)', 'def response_length(response):'),
            ('len(length) <= len(str(maximum))', 'len(length) <= 8'),
            ('0 < int(length) <= maximum', '0 < int(length) <= measurement.MAX_STREAM'),
        )
    else:
        raise AssertionError("unreviewed source normalization")
    for added, original in changes:
        if raw.count(added.encode()) != 1:
            raise AssertionError("opt-in source differs from exact reviewed addition")
        raw = raw.replace(added.encode(), original.encode())
    return raw
