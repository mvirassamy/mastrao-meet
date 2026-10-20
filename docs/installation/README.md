# Installation
If you want to install Mastrao Visio you've come to the right place.
These guides include upstream examples. Read the [Mastrao branding and email configuration](../branding.md) before deploying an instance.

## Kubernetes
See the [Kubernetes guide](/docs/installation/kubernetes.md) for the inherited deployment examples. Confirm the image and configuration for your Mastrao instance.

## Docker Compose
We understand that not everyone has a Kubernetes cluster available, please follow the instructions provided [here](/docs/installation/compose.md) to set up a docker compose instance.
The linked [upstream Docker images](https://hub.docker.com/u/lasuite?page=1&search=meet) are La Suite builds. Use the image built for this Mastrao fork when deploying Mastrao Visio.

## Scalingo

The upstream La Suite Meet project can be deployed on Scalingo PaaS using the Suite Numérique buildpack. See the [Scalingo deployment guide](./scalingo.md) for detailed instructions.

## Other upstream installation methods
Community members have contributed alternative ways to install La Suite Meet 🙏. While maintainers may not provide direct support, we help keep these instructions up to date, and you can reach out to contributors or the community for assistance.

Here is the list of other methods in alphabetical order:
- Nix: [Packages](https://search.nixos.org/packages?channel=unstable&show=lasuite-meet&query=lasuite-meet), ⚠️ unstable
- Yunohost: [Packages](https://github.com/YunoHost-Apps/meet_ynh), ⚠️ under construction (for small instances only)

> [!TIP]
> Feel free to make a PR to add ones that are not listed above

## Cloud providers
No Mastrao-specific cloud provider configuration is listed here.
> [!TIP]
> Feel free to make a PR to add ones that are not listed above
