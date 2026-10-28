# SPDX-License-Identifier: Apache-2.0
"""Exact reviewed-change normalization for historical-source assertions.

Only these literal profile additions and failed-usage accounting fixes may
differ. Everything left over is compared byte-for-byte (or by the existing
salt-only AST test) against immutable historical source. Runtime tests exercise
the changed behavior, including ceilings and malformed streams.
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
            ('            # Optional breakdowns can be malformed after valid totals arrive.\n'
             '            # Reject the stream, but do not turn already reported work into unknown usage.\n'
             '            self.usage = (prompt, output, None)\n', ''),
            ('            self.usage = (prompt, output, cached)\n'
             '            details = usage.get("completion_tokens_details")',
             '            details = usage.get("completion_tokens_details")'),
            ('            require(reasoning is None or integer(reasoning, output))\n',
             '            require(reasoning is None or integer(reasoning, output))\n'
             '            self.usage = (prompt, output, cached)\n'),
        )
    elif module == "stream_transport":
        changes = (
            ('# Closed selected-service wire identity; equality is checked against its owner\n'
             '# contract in tests. It is not a general provider/model-name override.\n'
             'SELECTED_MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"\n', ''),
            ('def model_binding(model, selected_model):\n'
             '    require(type(selected_model) is bool and type(model) is str)\n'
             '    require(model == SELECTED_MODEL if selected_model else re.fullmatch(r"[a-z0-9_.-]{1,128}", model))\n\n\n', ''),
            ('def request_body(messages, prompt_tokens, max_output_tokens, *, model=MODEL, cache_salt=None, selected_model=False):\n'
             '    model_binding(model, selected_model)',
             'def request_body(messages, prompt_tokens, max_output_tokens, *, model=MODEL, cache_salt=None):\n'
             '    require(type(model) is str and re.fullmatch(r"[a-z0-9_.-]{1,128}", model))'),
            ('    def __init__(self, endpoint, *, model=MODEL, selected_model=False):\n'
             '        require(type(endpoint) is identity.Endpoint and endpoint.port == PORT)\n'
             '        model_binding(model, selected_model)',
             '    def __init__(self, endpoint, *, model=MODEL):\n'
             '        require(type(endpoint) is identity.Endpoint and endpoint.port == PORT)\n'
             '        require(type(model) is str and re.fullmatch(r"[a-z0-9_.-]{1,128}", model))'),
            ('        self.selected_model = selected_model\n', ''),
            ('        raw = request_body(messages, prompt_tokens, max_output_tokens, model=self.model,\n'
             '                           cache_salt=cache_salt, selected_model=self.selected_model)',
             '        raw = request_body(messages, prompt_tokens, max_output_tokens, model=self.model, cache_salt=cache_salt)'),
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
            raise AssertionError("source differs from exact reviewed change")
        raw = raw.replace(added.encode(), original.encode())
    return raw
