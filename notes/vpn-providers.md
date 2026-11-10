# Choosing a VPN provider

Background for picking the VPN that moves the source IP between the US and France.
Prices and features were checked in October 2026, mostly from review and price-comparison sites that do not always agree; confirm on the provider's own pricing page before paying.

## Decision

On 2026-10-12 we chose Mullvad, at €5 a month, over NordVPN, which the original code was written for.

- NordVPN's command-line client exists only for Linux, and the experiment runs on a Mac. Making it work on macOS meant running OpenVPN by hand with NordVPN's config files, separate service credentials, and a sudo rule so that scheduled runs would not stop at a password prompt. We set that up partway and dropped it.
- Mullvad's macOS app includes a command-line client, so the runner switches country with two commands and no extra credentials or root access. The same commands work on Linux.
- It is the cheapest paid option for a one-month run: €5 against $12.99 for NordVPN's monthly plan.
- The account is a number, not an email address.
- Mullvad's app routes DNS through the tunnel; OpenVPN run by hand on macOS does not.

Tested on 2026-10-12 through the experiment's own `connect_vpn`: France (fr-mrs-wg-001, exit IP 138.199.15.174) and the US (us-nyc-wg-807, exit IP 23.234.103.73) were both verified by `ipapi.co`.

The rest of this note is the comparison behind the decision.

## What the experiment needs

- A server in the US and one in France, chosen explicitly. Plans that pick the server for you are useless here.
- Switching from a script. The runner calls a connect and a disconnect command (`vpn:` in `config.yaml`) and then checks the exit country with `ipapi.co`. A provider whose client can be driven from the terminal on the machine running the experiment saves the most work.
- macOS support, since the experiment runs on a Mac. Several providers only ship a command-line client for Linux.
- About two weeks of use: a 10-day run plus a pilot. A one-month plan is enough, so the monthly price matters more than the discounted multi-year price.
- Little data. A few dozen prompts a day through the API and three web apps amounts to far less than 1 GB in total.

## Comparison

| Provider | Monthly price | Command line on macOS | US and France | Notes |
|---|---|---|---|---|
| Mullvad | €5 (about $5.80) | Yes, `mullvad` ships with the app | Yes | Flat price, no discounts or renewals. Account is a number, no email. |
| Proton VPN Plus | about $9.99 | No (CLI is Linux only) | Yes | On macOS it would need OpenVPN or WireGuard config files run by hand, plus a sudo rule. |
| Proton VPN Free | free | No | No country choice | Server is assigned automatically; France does not appear to be among the free countries. |
| Private Internet Access | about $11.95 | Yes, `piactl` ships with the app | Yes | The GUI client must be running, or set to run in the background. |
| NordVPN | $12.99 (one source: $14.99) | No (CLI is Linux only) | Yes | The original setup; on macOS it would need OpenVPN config files. 30-day money-back guarantee. |
| ExpressVPN | about $12.95 | No command-line client found | Yes | Most expensive monthly plan, no advantage for this experiment. |
| Windscribe Free | free | No | Yes, both on the free plan | 10 GB/month with a confirmed email (2 GB without). Switching from a script would need extra work. |

## Mullvad setup

```bash
mullvad relay set location us
mullvad connect --wait
mullvad disconnect --wait
```

In `config.yaml`:

```yaml
connect_command: ["sh", "-c", "mullvad relay set location {target} && mullvad connect --wait"]
disconnect_command: ["mullvad", "disconnect", "--wait"]
```

with `vpn_target` set to `us` and `fr`. `--wait` makes each command return only once the tunnel is up or down.

The runner disconnects after each collection, on process signals, and when `vpn.max_session_seconds` elapses.
`python main.py vpn-off` disconnects immediately and soft-stops a stuck collector without clearing the active study.

## Other options

Private Internet Access is the fallback if a second macOS command-line option is wanted, at twice the price.
NordVPN, Proton VPN and ExpressVPN all work, but on macOS each needs OpenVPN config files, separate service credentials and a sudo rule, and NordVPN and ExpressVPN cost more than twice as much per month as Mullvad.

The free plans do not fit.
Proton's free tier cannot choose a country, and Windscribe's free tier has both countries, but no macOS command-line client turned up, so switching from a script would need extra work.

## Caveats that apply to any provider

- VPN exit IPs belong to data centres, not households. ChatGPT, Gemini and Claude sit behind bot protection that may show captchas or degrade service for known VPN addresses, independent of the country. A US-vs-France difference could partly reflect how each specific exit IP is treated. Recording the exit IP on every row (the runner already does) makes this checkable afterwards.
- Using one fixed server per country, rather than a random one per run, keeps the exit IP stable over the 10 days.

## Sources

- Mullvad CLI: [How to use the Mullvad CLI](https://mullvad.net/en/help/how-use-mullvad-cli)
- Mullvad price: [Security.org review](https://www.security.org/vpn/mullvad/), [CostBench](https://costbench.com/software/vpn/mullvad/calculator/)
- NordVPN price: [Security.org](https://www.security.org/vpn/nordvpn), [Tom's Guide](https://www.tomsguide.com/computing/vpns/nordvpn-price-plans), [NordVPN blog](https://nordvpn.com/ru/blog/how-much-does-a-vpn-cost/)
- Proton VPN price and free plan: [CostBench](https://costbench.com/software/vpn/protonvpn/), [CostBench free plan](https://costbench.com/software/vpn/protonvpn/free-plan/), [Security.org review](https://www.security.org/vpn/protonvpn/review/)
- Proton VPN Linux CLI: [release notes](https://protonvpn.com/support/release-notes-linux-cli)
- ExpressVPN price: [CostBench](https://www.costbench.com/software/vpn/expressvpn/), [TechRadar](https://www.techradar.com/vpn/vpn-services/expressvpn-just-beat-its-lowest-ever-prices-for-the-second-time-this-year-perfect-if-youre-traveling-for-st-patricks-day)
- Private Internet Access: [PIA command-line interface](https://helpdesk.privateinternetaccess.com/kb/articles/pia-desktop-command-line-interface-2), [CostBench](https://costbench.com/software/vpn/private-internet-access/)
- Windscribe free plan: [TechRadar review](https://www.techradar.com/reviews/windscribe-free-vpn), [PCWorld review](https://www.pcworld.com/article/3239292/software/windscribe-vpn-review.html)
- VPN IPs and bot protection: [OpenAI community thread](https://community.openai.com/t/how-to-unblock-myself-in-chatgpt/249876)

## Related

- [Root README](../README.md) (VPN setup steps)
- [docs/README.md](../docs/README.md) (all project notes)
