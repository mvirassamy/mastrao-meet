<p align="center">
  <img alt="Mastrao" src="./src/frontend/public/assets/mastrao-logo-icon.png" width="128">
</p>


<p align="center">
  <a href="https://github.com/mvirassamy/mastrao-meet/stargazers/">
    <img src="https://img.shields.io/github/stars/mvirassamy/mastrao-meet" alt="">
  </a>
  <a href='http://makeapullrequest.com'><img alt='PRs Welcome' src='https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=shields'/></a>
  <img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/mvirassamy/mastrao-meet"/>
  <img alt="GitHub closed issues" src="https://img.shields.io/github/issues-closed/mvirassamy/mastrao-meet"/>
  <a href="./LICENSES/LICENSE.MIT.md">
    <img alt="GitHub closed issues" src="https://img.shields.io/github/license/mvirassamy/mastrao-meet"/>
  </a>
</p>

<p align="center">
  <a href="https://livekit.io/">LiveKit</a> - <a href="https://github.com/mvirassamy/mastrao-meet/blob/main/CHANGELOG.md">Changelog</a> - <a href="https://github.com/mvirassamy/mastrao-meet/issues/new?assignees=&labels=bug&template=Bug_report.md">Bug reports</a>
</p>



## Mastrao Visio: Simple Video Conferencing

Mastrao Visio is the Mastrao fork of [Meet / La Suite Numérique](https://github.com/suitenumerique/meet), originally developed by DINUM and its contributors. Upstream attribution, copyrights and licenses are preserved.

See [Mastrao branding and French email configuration](./docs/branding.md) for instance-specific settings.

Powered by [LiveKit](https://livekit.io/), Mastrao Visio offers Zoom-level performance with high-quality video and audio. No installation required—simply join calls directly from your browser. Check out LiveKit's impressive optimizations in their [blog post](https://blog.livekit.io/livekit-one-dot-zero/).



### Features
- Optimized for stability in large meetings (+100 p.)
- Support for multiple screen sharing streams
- Non-persistent, secure chat
- End-to-end encryption (coming soon)
- Meeting recording
- Meeting transcription & Summary (currently in beta)
- Telephony integration
- Secure participation with robust authentication and access control
- Customizable frontend style
- LiveKit Advances features including :
  - speaker detection 
  - simulcast 
  - end-to-end optimizations 
  - selective subscription
  - SVC codecs (VP9, AV1)


Mastrao Visio is fully self-hostable and released under the MIT License, ensuring complete control and flexibility. Use the meeting link provided by your Mastrao organizer to join your instance.

We’re continuously adding new features to enhance your experience, with the latest updates coming soon!

## Table of Contents

- [Get started](#get-started)
- [Docs](#docs)
- [Self-host](#self-host)
- [Contributing](#contributing)
- [Philosophy](#philosophy)
- [Open source](#open-source)


## Get started

## Docs

We're currently working on both technical and user documentation for Mastrao Visio. In the meantime, many of the essential aspects are already well covered by the [LiveKit documentation](https://docs.livekit.io/home/) and their [self-hosting guide](https://docs.livekit.io/home/self-hosting/deployment/). Stay tuned for more updates!

## Self-host

### Mastrao Visio is easy to install on your own servers

The inherited installation guides cover Kubernetes and Docker Compose. Confirm the image and instance configuration before deploying this Mastrao fork. The community contributed a couple other methods (Nix, YunoHost etc.) check out the [docs](/docs/installation/README.md) to get detailed instructions and examples.

**Questions?** Open an issue on [GitHub](https://github.com/mvirassamy/mastrao-meet/issues/new?assignees=&labels=bug&template=Bug_report.md).

> [!NOTE]
> Some advanced features (ex: recording, transcription) lack detailed documentation. We're working hard to provide comprehensive guides soon.

#### Upstream instances
The following instances were listed by the upstream Meet project; they are not Mastrao instances or Mastrao support channels.

| Url                                                           | Org          | Access                                                                                                                                        |
|---------------------------------------------------------------|--------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| [visio.numerique.gouv.fr](https://visio.numerique.gouv.fr/)   | DINUM        | French public agents working for the central administration and the extended public sphere. ProConnect is required to login in or sign up     |
| [visio.suite.anct.gouv.fr](https://visio.suite.anct.gouv.fr/) | ANCT         | French public agents working for the territorial administration and the extended public sphere. ProConnect is required to login in or sign up |
| [visio.lasuite.coop](https://visio.lasuite.coop/)             | lasuite.coop | Free and open demo to all. Content and accounts are reset after one month                                                                     |
| [mosacloud.cloud](https://mosa.cloud/)                        | mosa.cloud   | Demo instance of mosa.cloud, a dutch company providing services around La Suite apps.                                                         |
| [Clever Cloud](https://www.clever.cloud/product/visio/)       | clever cloud | Openvisio is a sovereign video conferencing solution based on LaSuite Meet offered by [Clever Cloud](https://www.clever.cloud/).              |
| [Email.eu](https://email.eu/)                                 | Email.eu     | Sovereign business workspace.                                                                                                                 |


# Contributing

We <3 contributions of all kinds **big or small** and we’re genuinely glad you’re here. 🌱

### Start by saying hi

**The best first contribution is simply to come say hi.**

Before opening a larger PR, discuss the change with this fork’s maintainers through [GitHub issues](https://github.com/mvirassamy/mastrao-meet/issues).

Getting in touch early helps us align on goals, avoid duplicated or wasted effort, and build a community that stays active, welcoming, and fun to be part of. There are no silly questions here: whether you’ve shipped hundreds of PRs or you’re just getting started, you’re welcome.

### AI contributions

AI-assisted contributions are welcome. But code is never the end goal. What matters most is building relationships, sharing knowledge, and growing a sustainable community over time.

If your contribution has been heavily generated with AI, please be transparent about it. This helps maintainers review it with the right context and respects the time they invest in the project.

Using AI does not transfer ownership of the contribution: you should still fully understand the code, the problem it solves, and the reasoning behind the approach you propose. In short, even if AI helped write it, the why should still be yours.

### Contributions beyond code

**Not technical? We need you too.**
Open source is much more than code. Writing documentation, improving onboarding, translating content, answering questions, reporting bugs, or simply helping others feel welcome all make a huge difference.

### Ways to contribute

When you’re ready, here are a few ways to get involved:

* 👋 **Say hello** and share your ideas with this fork’s maintainers through [GitHub issues](https://github.com/mvirassamy/mastrao-meet/issues)
* 🛠️ **Open a PR** by following our guide to [develop Mastrao Visio locally](https://github.com/mvirassamy/mastrao-meet/blob/main/docs/developping_locally.md)
* 💡 **Suggest an idea** by opening a [feature request](https://github.com/mvirassamy/mastrao-meet/issues/new?assignees=&labels=enhancement&template=Feature_request.md)
* 🐛 **Report a bug** by opening a [bug report](https://github.com/mvirassamy/mastrao-meet/issues/new?assignees=&labels=bug&template=Bug_report.md)

Thank you for helping build something open, useful, and human. 💙

The upstream project also has a [Meet community channel](https://matrix.to/#/#meet-official:matrix.org). It is separate from Mastrao support.

## Philosophy

We’re relentlessly focused on building the best open-source video conferencing product—Mastrao Visio. Growth comes from creating something people truly need, not just from chasing metrics.

Our users come first. We’re committed to making Mastrao Visio as accessible and easy to use as proprietary solutions, ensuring it meets the highest standards.

Most of the heavy engineering is handled by the incredible LiveKit team, allowing us to focus on delivering a top-tier product. We follow extreme programming practices, favoring pair programming and quick, iterative releases. Challenge our tech and architecture—simplicity is always our top priority.


## Open-source

This fork retains the original [MIT license](./LICENSES/LICENSE.MIT.md) and DINUM/Etalab copyright from [suitenumerique/meet](https://github.com/suitenumerique/meet).

All features we develop will always remain open-source, and we are committed to contributing back to the LiveKit community whenever feasible.
Discuss this fork with its maintainers through [GitHub issues](https://github.com/mvirassamy/mastrao-meet/issues).

### Help us!

Come help us make Mastrao Visio even better. Contributions to this fork are welcome through [GitHub](https://github.com/mvirassamy/mastrao-meet).


## Contributors 🧞

<a href="https://github.com/suitenumerique/meet/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=suitenumerique/meet" />
</a>

## Credits 

We're using the awesome [LiveKit](https://livekit.io/) implementation. We're also thankful to the teams behind [Django Rest Framework](https://www.django-rest-framework.org/), [Vite.js](https://vite.dev/), and [React Aria](https://github.com/adobe/react-spectrum) — Thanks for your amazing work!
This project is tested with BrowserStack.

## License

The original code was published under the MIT license by DINUM (Direction interministériel du numérique). This fork preserves that attribution and copyright.
Documentation (in the docs/) directory is released under the [Etalab-2.0 license](https://spdx.org/licenses/etalab-2.0.html).

