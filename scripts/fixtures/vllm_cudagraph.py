# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Unchanged definitions from vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be:
# vllm/config/compilation.py and vllm/v1/worker/gpu/cudagraph_utils.py.
# https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be
# Methods are dedented for CPU-only testing without importing Torch/CUDA.
from __future__ import annotations

import enum
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby, product


def round_up(n, k):
    return ((n + k - 1) // k) * k


class CUDAGraphMode(enum.Enum):
    """Constants for the cudagraph mode in CompilationConfig.
    Meanwhile, the subset enum `NONE`, `PIECEWISE` and `FULL` are also
    treated as concrete runtime mode for cudagraph runtime dispatching.
    """

    NONE = 0
    PIECEWISE = 1
    FULL = 2
    FULL_DECODE_ONLY = (FULL, NONE)
    FULL_AND_PIECEWISE = (FULL, PIECEWISE)

    def decode_mode(self) -> "CUDAGraphMode":
        return CUDAGraphMode(self.value[0]) if self.separate_routine() else self

    def mixed_mode(self) -> "CUDAGraphMode":
        return CUDAGraphMode(self.value[1]) if self.separate_routine() else self

    def has_mode(self, mode: "CUDAGraphMode") -> bool:
        assert not mode.separate_routine()
        if self.separate_routine():
            return mode.value in self.value
        return self == mode

    def requires_piecewise_compilation(self) -> bool:
        return self.has_mode(CUDAGraphMode.PIECEWISE)

    def without_piecewise(self) -> "CUDAGraphMode":
        if self == CUDAGraphMode.PIECEWISE:
            return CUDAGraphMode.NONE
        if self == CUDAGraphMode.FULL_AND_PIECEWISE:
            return CUDAGraphMode.FULL_DECODE_ONLY
        return self

    def max_cudagraph_mode(self) -> "CUDAGraphMode":
        return CUDAGraphMode(max(self.value)) if self.separate_routine() else self

    def has_full_cudagraphs(self) -> bool:
        return self.max_cudagraph_mode() == CUDAGraphMode.FULL

    def has_piecewise_cudagraphs(self) -> bool:
        return self.requires_piecewise_compilation()

    def separate_routine(self) -> bool:
        return isinstance(self.value, tuple)

    @classmethod
    def valid_runtime_modes(cls) -> frozenset["CUDAGraphMode"]:
        return frozenset({cls.NONE, cls.PIECEWISE, cls.FULL})

    def is_valid_runtime_mode(self) -> bool:
        return self in CUDAGraphMode.valid_runtime_modes()

    def __str__(self) -> str:
        return self.name

    def __bool__(self) -> bool:
        return self != CUDAGraphMode.NONE


@dataclass(frozen=True)
class BatchExecutionDescriptor:
    """Describes the shape of the batch and CG mode to run; this is used to make shape
    matches between the capture and runtime."""

    cg_mode: CUDAGraphMode
    num_tokens: int
    num_reqs: int | None  # None means no request padding is needed (PIECEWISE graphs)
    uniform_token_count: int | None = None
    # Upper bound on per-request query length. Varlen decode graphs leave
    # uniform_token_count unset, so this is what keeps a prefill batch out of one:
    # the runner passes None for any batch with a prefill.
    max_query_len: int | None = None
    num_active_loras: int = 0
    # Number of microbatches the batch is split into (DBO). 1 means no splitting.
    num_ubatches: int = 1


def _is_compatible(
    desc: BatchExecutionDescriptor,
    num_reqs: int,
    num_tokens: int,
    uniform_token_count: int | None,
    num_active_loras: int,
    max_query_len: int | None,
    num_ubatches: int,
) -> bool:
    # desc.uniform_token_count=None (PIECEWISE) can handle any uniform_token_count
    # desc.num_reqs=None means no request padding needed (PIECEWISE)
    # desc.max_query_len=None means the graph does not constrain query length; a
    # caller that does not track max_query_len must not match one that does
    # A graph captured for N microbatches can only serve a batch split N ways.
    return (
        (
            desc.uniform_token_count is None
            or desc.uniform_token_count == uniform_token_count
        )
        and (
            desc.max_query_len is None
            or (max_query_len is not None and desc.max_query_len >= max_query_len)
        )
        and (desc.num_reqs is None or desc.num_reqs >= num_reqs)
        and desc.num_tokens >= num_tokens
        and desc.num_active_loras == num_active_loras
        and desc.num_ubatches == num_ubatches
    )


def _init_candidates(self) -> None:
    """Build priority-ordered candidate lists for each token count."""
    capture_sizes = self.compilation_config.cudagraph_capture_sizes
    if not (self.cudagraph_mode and capture_sizes):
        return

    capture_sizes = sorted(capture_sizes)
    max_decode_tokens = self.max_num_reqs * self.decode_query_len
    decode_mode = self.cudagraph_mode.decode_mode()
    mixed_mode = self.cudagraph_mode.mixed_mode()
    separate_decode_routine = self.cudagraph_mode.separate_routine()
    max_cg_capture_size = self.compilation_config.max_cudagraph_capture_size

    descs_by_mode: defaultdict[CUDAGraphMode, list[BatchExecutionDescriptor]] = (
        defaultdict(list)
    )

    # When using Dynamic SD, num_speculative_tokens is the max number of
    # draft tokens. The scheduler might use a smaller number so we need
    # to capture graphs for all possible values during decode.
    speculative_config = self.vllm_config.speculative_config
    if (
        speculative_config
        and speculative_config.uses_dynamic_speculative_decoding()
        and self.decode_query_len > self.vllm_config.num_speculative_tokens
    ):
        # decode_query_len = num_speculative_steps + num_new_sampled_tokens
        # _per_step. Recover num_new_sampled_tokens_per_step
        # from the values the manager already has.
        num_new_sampled_tokens_per_step = (
            self.decode_query_len - self.vllm_config.num_speculative_tokens
        )
        dense_schedule = build_dynamic_sd_schedule_lookup(
            speculative_config.num_speculative_tokens_per_batch_size,
            vllm_max_batch_size=self.max_num_reqs,
            vllm_num_speculative_tokens=self.vllm_config.num_speculative_tokens,
        )
        decode_query_lens = sorted(
            {
                num_spec + num_new_sampled_tokens_per_step
                for num_spec in dense_schedule[1:]
            }
        )
    else:
        decode_query_lens = [self.decode_query_len]

    capture_varlen_decode = (
        separate_decode_routine and bool(decode_mode) and self.varlen_decode
    )
    for num_tokens, num_active_loras in product(
        capture_sizes, self.lora_capture_cases
    ):
        # Varlen decode graphs take any mix of 1..decode_query_len tokens per
        # request, worst case 1 token per request (or max_num_reqs)
        if capture_varlen_decode and num_tokens <= max_decode_tokens:
            desc = BatchExecutionDescriptor(
                cg_mode=decode_mode,
                num_tokens=num_tokens,
                num_reqs=min(num_tokens, self.max_num_reqs),
                max_query_len=self.decode_query_len,
                num_active_loras=num_active_loras,
            )
            descs_by_mode[decode_mode].append(desc)
        # Capture uniform decode specfifc graphs if required
        #  (i.e. separate decode routine)
        elif separate_decode_routine and decode_mode and not self.varlen_decode:
            for decode_query_len in decode_query_lens:
                rounded_num_tokens = round_up(num_tokens, decode_query_len)
                rounded_num_reqs = rounded_num_tokens // decode_query_len

                if (
                    rounded_num_tokens > max_decode_tokens
                    or rounded_num_tokens > max_cg_capture_size
                    or rounded_num_reqs > self.max_num_reqs
                ):
                    continue

                desc = BatchExecutionDescriptor(
                    cg_mode=decode_mode,
                    num_tokens=rounded_num_tokens,
                    num_reqs=rounded_num_reqs,
                    uniform_token_count=decode_query_len,
                    num_active_loras=num_active_loras,
                )

                # avoid duplicate graphs
                if desc not in descs_by_mode[decode_mode]:
                    descs_by_mode[decode_mode].append(desc)

                ubatch_desc = self._maybe_ubatch_twin(desc)
                if ubatch_desc is not None and (
                    ubatch_desc not in descs_by_mode[decode_mode]
                ):
                    descs_by_mode[decode_mode].append(ubatch_desc)

        # recoverSSM cannot capture a dummy query wider than its workspace.
        if mixed_mode and (
            not self.vllm_config.cache_config.use_kda_recoverssm
            or num_tokens <= max_decode_tokens
        ):
            # for PIECEWISE graphs there is no limit on requests when replaying
            # i.e. no request padding is needed, so we leave it as None.
            # For breakable PW graphs, break-point kernels read the real batch
            # from the forward context; in-graph kernels handle the token padding
            # themselves from the padded slot_mapping (rows with slot == -1).
            num_reqs = None
            if mixed_mode == CUDAGraphMode.FULL:
                num_reqs = min(num_tokens, self.max_num_reqs)
            desc = BatchExecutionDescriptor(
                cg_mode=mixed_mode,
                num_tokens=num_tokens,
                num_reqs=num_reqs,
                num_active_loras=num_active_loras,
            )
            descs_by_mode[mixed_mode].append(desc)

            ubatch_desc = self._maybe_ubatch_twin(desc)
            if ubatch_desc is not None:
                descs_by_mode[mixed_mode].append(ubatch_desc)

    for mode, descs in descs_by_mode.items():
        descs.sort(key=lambda d: d.num_tokens, reverse=True)
        self._capture_descs[mode] = descs

    for mode in (CUDAGraphMode.FULL, CUDAGraphMode.PIECEWISE):
        mode_descs = tuple(reversed(descs_by_mode.get(mode, [])))
        for num_active_loras in self.lora_capture_cases:
            lora_descs = [
                d for d in mode_descs if d.num_active_loras == num_active_loras
            ]
            current_range_start = 0
            # Dynamic speculative decoding can produce multiple graphs with the same
            # num_tokens. Group them so each graph covers the same candidate range.
            for num_tokens, group in groupby(lora_descs, lambda d: d.num_tokens):
                matching = list(group)
                for i in range(current_range_start, num_tokens + 1):
                    key = (i, num_active_loras)
                    self._candidates.setdefault(key, []).extend(matching)
                current_range_start = num_tokens + 1


def dispatch(
    self,
    num_reqs: int,
    num_tokens: int,
    uniform_token_count: int | None,
    num_active_loras: int,
    max_query_len: int | None = None,
    num_ubatches: int = 1,
) -> BatchExecutionDescriptor:
    """Find matching cudagraph descriptor from priority-ordered candidates."""
    effective_loras = self._resolve_effective_loras(num_active_loras)
    key = (num_tokens, effective_loras)
    if self._graphs_captured and num_tokens > 0 and key in self._candidates:
        for desc in self._candidates[key]:
            if _is_compatible(
                desc,
                num_reqs,
                num_tokens,
                uniform_token_count,
                effective_loras,
                max_query_len,
                num_ubatches,
            ):
                return desc
    return BatchExecutionDescriptor(
        cg_mode=CUDAGraphMode.NONE,
        num_tokens=num_tokens,
        num_reqs=num_reqs,
        num_active_loras=effective_loras,
        num_ubatches=num_ubatches,
    )
