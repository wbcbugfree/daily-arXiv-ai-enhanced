# 🚀 daily-arXiv-ai-enhanced


This innovative tool transforms how you stay updated with arXiv papers by combining automated crawling with AI-powered summarization.


## ✨ Key Features

🎯 **Zero Infrastructure Required**
- Leverages GitHub Actions and Pages - no server needed
- Completely free to deploy and use

🤖 **Smart AI Summarization**
- Daily paper crawling with DeepSeek-powered summaries
- Cost-effective: Only ~0.2 CNY per day

💫 **Smart Reading Experience**
- Personalized paper highlighting based on your interests
- Cross-device compatibility (desktop & mobile)
- Local preference storage for privacy
- Flexible date range filtering

🧩 **SKILL System**
- Plug-and-play skill modules for customizing paper filtering

⚙️ **Easy Preference Export & Integration**
- One-click copy in Settings to export your keywords and authors configuration
- Seamlessly combine exported preferences with SKILL for reproducible and shareable setups

👉 **[Try it now!](https://wbcbugfree.github.io/daily-arXiv-ai-enhanced/)** - No installation required



https://github.com/user-attachments/assets/b25712a4-fb8d-484f-863d-e8da6922f9d7



# How to use
If you wish to crawl other arXiv categories, use other LLMs, or other languages, please follow the instructions.

## Instructions
1. Fork this repo to your own account.
2. Go to: your-own-repo -> Settings -> Secrets and variables -> Actions
3. Go to Secrets. Secrets are encrypted and used for sensitive data
4. Create two repository secrets named `OPENAI_API_KEY` and `OPENAI_BASE_URL`, and input corresponding values.
5. [Optional] Set a password in `secrets.ACCESS_PASSWORD` if you do not wish others to access your page. (see https://github.com/dw-dengwei/daily-arXiv-ai-enhanced/pull/64)
6. Go to Variables. Variables are shown as plain text and are used for non-sensitive data
7. Create the following repository variables:
   1. `CATEGORIES`: separate the categories with ",", such as "cs.CL, cs.CV"
   2. `LANGUAGE`: such as "Chinese" or "English"
   3. `MODEL_NAME`: such as "deepseek-chat"
   4. `EMAIL`: your email for push to GitHub
   5. `NAME`: your name for push to GitHub
   6. [Optional] `API_MODE`: `auto` (default), `responses`, or `chat_completions`. `auto` uses Responses for `api.openai.com` and preserves Chat Completions for OpenRouter and other compatible providers.
   7. [Optional] `REASONING_EFFORT`: applies only to Responses requests. Leave unset to use the model's default (for example, `medium` for `gpt-6-luna`), or set an effort supported by your model, such as `low`, `medium`, or `high`.
8. Go to your-own-repo -> Actions -> arXiv-daily-ai-enhanced
9. You can manually click **Run workflow** to test if it works well (it may take about one hour). By default, this action will automatically run every day. You can modify it in `.github/workflows/run.yml`
10. Set up GitHub pages: Go to your own repo -> Settings -> Pages. In `Build and deployment`, set `Source="Deploy from a branch"`, `Branch="main", "/(root)"`. Wait for a few minutes, go to https://\<username\>.github.io/daily-arXiv-ai-enhanced/. Please see this [issue](https://github.com/dw-dengwei/daily-arXiv-ai-enhanced/issues/14) for more precise instructions.

### Model API compatibility

| Provider | `OPENAI_BASE_URL` secret | `MODEL_NAME` variable | `API_MODE` variable |
| --- | --- | --- | --- |
| OpenRouter free models | `https://openrouter.ai/api/v1` | Your existing `:free` model ID | `auto` or `chat_completions` |
| OpenAI directly | `https://api.openai.com/v1` | `gpt-6-luna` | `auto` or `responses` |
| OpenRouter Responses | `https://openrouter.ai/api/v1` | A model/provider supporting structured outputs | `responses` |

Use the selected provider's key for `OPENAI_API_KEY`. Changing `API_MODE` does not change the provider, model, or billing account. OpenAI promotional API credits apply to direct OpenAI calls, not ordinary OpenRouter billing.

Responses requests use JSON Schema for the same five analysis fields and set `store: false`, without sharing conversation state between papers. OpenRouter's Responses endpoint is stateless, and structured-output support depends on the selected model/provider. The default OpenRouter path keeps the existing function-calling behavior. See the [OpenAI Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses), [Luna model reference](https://developers.openai.com/api/docs/models/gpt-6-luna), [OpenRouter Responses guide](https://openrouter.ai/docs/api_reference/responses/overview), and [OpenRouter structured-output guide](https://openrouter.ai/docs/guides/features/structured-outputs).

The AI step validates required configuration before making requests. Authentication, explicit credit/quota errors, and recognized model/endpoint configuration errors fail the batch and cancel queued work. Transient errors still use the existing retries (including `Retry-After`), and individual papers can retain fallback fields or usable partial analysis. If every paper fails, the workflow fails instead of publishing an all-placeholder batch. An existing enhanced output is preserved when processing fails.
