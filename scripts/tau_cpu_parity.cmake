# SPDX-License-Identifier: Apache-2.0
# Included after the unmodified pinned vendor CMakeLists.txt in a private copy.
add_executable(nib-tau-tokenize "${CMAKE_CURRENT_LIST_DIR}/tau_cpu_parity.cpp")
target_link_libraries(nib-tau-tokenize PRIVATE llama-common llama)
target_compile_features(nib-tau-tokenize PRIVATE cxx_std_17)
set_target_properties(nib-tau-tokenize PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
