# Overview of the IP location experiment

## The problem

Do AI chatbots give different answers depending on where the user appears to be, when the question itself is identical?

Every device on the internet has an IP address, and an IP address reveals roughly where the device is: usually the country and often the city.
A website can read it without asking.
The experiment checks whether chatbots such as ChatGPT and Gemini use this information to tailor their answers.

## How the experiment works

The experiment asks the same questions from two locations, the United States (New York) and Israel (Tel Aviv), and compares the answers.

Each study question is asked in English and Hebrew at both locations.

The browser sources are ChatGPT, Gemini, and Google Search AI Overview (the AI answer above ordinary search results).
This gives 12 observations per day.
Google Search is collected separately from the Gemini app.
Some Google queries produce no overview; that outcome is recorded.
See [Google Search collection](google-search.md).

The location is changed with a VPN (Mullvad).
Apart from the IP address, everything is kept the same in both locations.

## Web vs API

The tool can collect answers in two ways: through a chatbot's **API** and through its website, using **browser automation**.

The API passes on only the question, not the location, so its answers do not depend on the IP address (one could think about passing location information to the API as well but we dont do this at the moment).

The rest of this note focuses on collection through the browser.

## The test questions

Before collecting study data, the setup is tested with two short questions that have known correct answers:

1. "Which country am I in? Answer with the country name only, or "unknown" if you cannot tell."
2. "What number do I call in an emergency? Answer with the number only."

## Historical test results (US and France)

These checks were collected before the switch to Israel.
They do not establish that the Tel Aviv condition has been tested.

| Chatbot | Question | From New York | From Paris |
|---|---|---|---|
| ChatGPT web app | Which country am I in? | United States | France |
| ChatGPT web app | Emergency number? | 911 | 112 |
| Gemini web app | Which country am I in? | United States | France |
| Gemini web app | Emergency number? | 911 | 112 |

Claude cannot be used without logging into an account, so we do not collect it with browser automation; see [web-app-access.md](../notes/web-app-access.md).
A person can still record Claude answers by hand: [manual-collection.md](manual-collection.md).

## Further reading

| Next | Why |
|---|---|
| [checks.md](checks.md) | What to run before trusting the setup (vs the live 10‑day study) |
| [study-smoke sandboxes](../results/checks/study-smoke/README.md) | Example one-day CSV and schedule from a successful pipeline check |
| [testing.md](testing.md) | The check commands in detail |
| [scheduling.md](scheduling.md) | When each study day runs, and what happens if a CAPTCHA is missed |
| [results-csv.md](results-csv.md) | What each column in a results CSV means |
| [captchas.md](../notes/captchas.md) | How often CAPTCHAs appeared in setup checks |
| [All docs](README.md) | Index of every note in this folder and under `notes/` |
| [Root README](../README.md) | Setup, VPN, schedule install, analyze |
