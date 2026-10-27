# SPDX-License-Identifier: Apache-2.0
"""Exact opt-in normalization for historical-source regression assertions.

Only these literal, reviewed opt-in additions may differ. Everything left over is
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
            raise AssertionError("opt-in source differs from exact reviewed addition")
        raw = raw.replace(added.encode(), original.encode())
    return raw
