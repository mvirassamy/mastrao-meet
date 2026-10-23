"""Pages rendered by the server carry the Mastrao look and icon."""

MASTRAO_ICON = '<link rel="icon" type="image/png" href="/assets/mastrao-logo-icon.png">'


def test_unknown_page_renders_the_mastrao_not_found_page(client):
    """An unknown address shows the French Mastrao page, not Django's default."""
    response = client.get("/page-inconnue/")

    assert response.status_code == 404
    content = response.content.decode()
    assert "Cette page n’existe pas" in content
    assert '<a class="button" href="/">Revenir à l’accueil</a>' in content
    assert MASTRAO_ICON in content
    assert "Not Found" not in content
