// SPDX-License-Identifier: Apache-2.0
// Public common APIs at llama.cpp 620d2225cc436ab0b001e3200e590e3b286ef9ab.
#include "chat.h"
#include "common.h"
#include "llama.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <memory>
#include <stdexcept>
#include <string>

static void require(bool value) {
    if (!value) {
        throw std::runtime_error("rejected");
    }
}

static void write_all(int fd, const std::string & value) {
    size_t offset = 0;
    while (offset < value.size()) {
        const ssize_t count = write(fd, value.data() + offset, value.size() - offset);
        require(count > 0);
        offset += static_cast<size_t>(count);
    }
}

int main(int argc, char ** argv) {
    // Only the dedicated parent pipe receives IDs. Native logging and exception
    // text cannot become a report, even if a dependency bypasses its logger.
    const int report = dup(STDOUT_FILENO);
    const int sink = open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (report < 0 || sink < 0 || fcntl(report, F_SETFD, FD_CLOEXEC) < 0
            || dup2(sink, STDOUT_FILENO) < 0 || dup2(sink, STDERR_FILENO) < 0) {
        return 1;
    }
    close(sink);
    try {
        struct stat input_info {}, output_info {};
        require(argc == 2 && fstat(STDIN_FILENO, &input_info) == 0
                && S_ISFIFO(input_info.st_mode) && fstat(report, &output_info) == 0
                && S_ISFIFO(output_info.st_mode));
        std::string raw;
        std::array<char, 4096> chunk {};
        while (true) {
            const ssize_t count = read(STDIN_FILENO, chunk.data(), chunk.size());
            require(count >= 0);
            if (count == 0) {
                break;
            }
            raw.append(chunk.data(), static_cast<size_t>(count));
            require(raw.size() <= 65536);
        }
        const auto probes = common_json::parse(raw);
        require(probes.is_array() && probes.size() == 4);
        llama_backend_init();
        auto params = llama_model_default_params();
        params.vocab_only = true;
        params.n_gpu_layers = 0;
        std::unique_ptr<llama_model, decltype(&llama_model_free)> model(
            llama_model_load_from_file(argv[1], params), llama_model_free);
        require(model != nullptr);
        const llama_vocab * vocab = llama_model_get_vocab(model.get());
        const auto templates = common_chat_templates_init(model.get(), "");
        common_json rows = common_json::array();
        for (const auto & probe : probes) {
            require(probe.is_object() && probe.contains("messages")
                    && (probe.size() == 1 || (probe.size() == 2 && probe.contains("tools")))
                    && probe.at("messages").is_array()
                    && !probe.at("messages").empty() && probe.at("messages").size() <= 4);
            for (const auto & message : probe.at("messages")) {
                require(message.is_object() && message.contains("role")
                        && message.at("role").is_string() && message.contains("content")
                        && (message.at("content").is_string() || message.at("content").is_null()));
            }
            common_chat_templates_inputs inputs;
            inputs.messages = common_chat_msgs_parse_oaicompat(probe.at("messages"));
            inputs.tools = common_chat_tools_parse_oaicompat(probe.value("tools", common_json()));
            inputs.use_jinja = true;
            inputs.add_generation_prompt = true;
            inputs.continue_final_message = COMMON_CHAT_CONTINUATION_NONE;
            inputs.tool_choice = COMMON_CHAT_TOOL_CHOICE_AUTO;
            inputs.parallel_tool_calls = false;
            inputs.reasoning_format = COMMON_REASONING_FORMAT_DEEPSEEK;
            inputs.enable_thinking = false;
            inputs.chat_template_kwargs = {{"enable_thinking", "false"}};
            // Same public template/session API as the server, independently
            // invoked on the parent probes, never HTTP-derived rendered text.
            const common_chat_session session(templates.get(), vocab, inputs);
            const auto tokens = common_tokenize(vocab, session.prompt().str(), true, true);
            require(!tokens.empty() && tokens.size() <= 1024);
            for (const auto token : tokens) {
                require(token >= 0);
            }
            rows.push_back(tokens);
        }
        const auto encoded = common_json({{"schema", "nib.cpu-parity-helper/v1"}, {"tokens", rows}}).dump();
        require(encoded.size() <= 65536);
        write_all(report, encoded);
        close(report);
        return 0;
    } catch (...) {
        close(report);
        return 1;
    }
}
