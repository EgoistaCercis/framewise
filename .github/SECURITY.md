# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in FrameWise, please **do not** open a public issue.

Instead, send an email to the project maintainer with details of the vulnerability:

- Describe the vulnerability and its potential impact
- Include steps to reproduce
- If possible, suggest a fix

You should receive a response within 48 hours. After the vulnerability is patched, a public disclosure will be made (if appropriate).

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest `main` branch | ✅ |
| Older versions | ❌ |

## Security Best Practices for Users

- **Never commit your `.env` file** — it's already in `.gitignore`
- Use strong, unique API keys for each provider
- Review the `.env.example` to understand what each key does
- If deploying publicly, consider using a reverse proxy (nginx) with HTTPS
