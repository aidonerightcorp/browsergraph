# Security

## Reporting

Open a [private security advisory](https://github.com/aidonerightcorp/browsergraph/security/advisories/new).
Please do not open a public issue for a vulnerability.

## Scope worth knowing about

This library **drives browsers and fetches pages you point it at**, so a few things are
inherent rather than defects:

* **Graph configs execute nodes.** `browsergraph run graph.yaml` instantiates registered
  node classes and, with LLM nodes enabled, may evaluate model-produced selectors. Treat a
  graph file like code: do not run one you did not write.
* **`--no-sandbox` in containers.** Chrome's sandbox cannot initialise as root, which is
  every Docker/CI/Kaggle environment. The flag is added automatically there. It is not a
  protection being given up — it was never available — but a browser without a sandbox
  should not be pointed at untrusted pages on a host you care about.
* **Stealth features.** Fingerprint and TLS impersonation exist for testing your own
  properties and for lawful data collection. Respect `robots.txt` and terms of service;
  the built-in limiter honours `Crawl-delay` and defaults to being polite.
* **Credentials.** `Identity.proxy` and `LLMConfig.api_key` are held in memory and sent to
  the host you configure. `LLMConfig.from_env()` reads `OLLAMA_API_KEY` so keys need not
  appear in source.
