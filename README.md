# Ace

## Install

```bash
uv sync
uv run playwright install chromium
```

---

## API Keys

Pick one provider and get a key:

| Provider | Free? | Link |
|---|---|---|
| **Groq** (Llama 4 Scout) | Free tier | https://console.groq.com |
| **DeepSeek** | ~$0.001/run | https://platform.deepseek.com |
| **Anthropic** | Paid | https://console.anthropic.com |

---

## Setup

```bash
# Groq (free)
ace config set provider groq
ace config set groq_api_key gsk_xxxx

# DeepSeek (cheap)
ace config set provider deepseek
ace config set deepseek_api_key sk-xxxx

# Anthropic
ace config set provider anthropic
ace config set anthropic_api_key sk-ant-xxxx
```

---

## Commands

```bash
ace run                  # open browser, go to assignment, press Enter
ace run --dry-run        # fill answers but don't submit
ace run --url <url>      # jump straight to an assignment URL

ace config show          # show current provider, models, keys (masked)
ace config set <key> <value>

ace debug                # show what Ace sees in the current browser tab
```

### `ace config set` keys

| Key | Example |
|---|---|
| `provider` | `groq` \| `deepseek` \| `anthropic` |
| `groq_api_key` | `gsk_...` |
| `groq_model` | `meta-llama/llama-4-scout-17b-16e-instruct` |
| `deepseek_api_key` | `sk-...` |
| `deepseek_model` | `deepseek-chat` |
| `anthropic_api_key` | `sk-ant-...` |
| `anthropic_model` | `claude-sonnet-4-5` |

Config is stored in `~/.ace/.env`.
