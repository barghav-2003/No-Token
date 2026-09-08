founder-labs-claude-code-antigravity-guide.pdf
PDF
User: I want the exact process 
I want to use Claude in my system as the doc says for free
analyse the doc and tell me the steps

Assistant: I analyzed the guide. The important takeaway is that the document does not show a way to use Anthropic Claude models for free. In fact, it explicitly says the opposite: OpenRouter has free models, but Anthropic Claude models are usage-billed, and this setup does not give you a free Claude Pro plan. 


What this guide is actually doing
It configures Claude Code (the coding extension) to send requests through OpenRouter instead of directly to Anthropic.

That means:

Antigravity IDE
        │
Claude Code Extension
        │
OpenRouter API
        │
Claude / GPT / Gemini / DeepSeek / etc.
Whether it's free depends on which model you select on OpenRouter, not on the extension itself.

Step 1: Install Antigravity IDE
Download the IDE for your operating system (Windows/macOS/Linux). 


Step 2: Install the Claude Code extension
Inside Antigravity IDE:

Open Extensions

Search for Claude Code

Install the one published by Anthropic. 


Step 3: Create an OpenRouter account
Go to:

https://openrouter.ai

Create an account.

Step 4: Generate an API Key
Open:

Settings → API Keys

Create a new key.

It will look similar to:

sk-or-v1-xxxxxxxxxxxxxxxx
Copy it immediately because OpenRouter only shows it once. The guide also warns not to publish or commit the key. 


Step 5: Create the Claude configuration
Inside your project create:

.claude/
    settings.local.json
Paste:

{
  "env": {
    "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
    "ANTHROPIC_AUTH_TOKEN": "sk-or-v1-YOUR-KEY",
    "ANTHROPIC_API_KEY": ""
  }
}
Replace:

sk-or-v1-YOUR-KEY
with your actual OpenRouter API key. 


Step 6: Restart the IDE
Close Antigravity completely.

Open it again.

Step 7: If you were previously logged into Anthropic
Run:

/logout
Restart the IDE.

Then run:

/status
The guide recommends this to verify Claude Code is using the intended configuration. 


Step 8: Start coding
You can now use Claude Code in the IDE.

Can you actually use Claude for free?
This is the critical part.

The guide says:

✅ OpenRouter provides some free models for learning.

❌ Anthropic Claude models are not free through this setup.

❌ This does not provide a free Claude Pro subscription. 


So if you select:

Claude Sonnet
Claude Opus
Claude Haiku (paid endpoint)
OpenRouter will charge your account based on usage.

If you instead select a free model (for example, certain free DeepSeek, Qwen, or other community models when available), those requests may not incur charges, but you would not be using Anthropic Claude.

If your goal is specifically "Claude Code for free"
The answer is:

There is no officially supported method in this guide to use Anthropic Claude models for free.

The setup lets Claude Code communicate through OpenRouter, but using Anthropic Claude models still requires paid usage.

If you saw a video claiming "Claude Code for free," it may have been referring to using free OpenRouter models with the Claude Code interface, not free access to Anthropic's Claude models themselves.

User: Ok then are there any free alternatives to do so? Give me the free and best alternatives?