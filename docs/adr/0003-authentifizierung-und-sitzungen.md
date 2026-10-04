# ADR 0003: Authentifizierung über OIDC mit serverseitigen Sitzungen

Status: angenommen

## Entscheidung
* Anmeldung nur über OpenID Connect (Keycloak) im Authorization-Code-Flow mit PKCE (S256), `state` und `nonce`. Der Browser sieht keine Tokens.
* Der Callback prüft Signatur (JWKS), Issuer, Audience, Ablauf und Nonce des ID-Tokens. Der Anmeldeversuch (`identity.login_attempts`) ist kurzlebig und wird genau einmal verbraucht.
* Danach legt die API eine eigene Sitzung an. Das Cookie enthält einen zufälligen, undurchsichtigen Wert (mindestens 256 Bit); in der Datenbank steht nur dessen SHA-256. Das ID-Token wird für das Abmelden am Provider (RP-initiated logout) mit Fernet verschlüsselt abgelegt.
* Cookies: `HttpOnly`, `SameSite=Lax`, `Secure` und Präfix `__Host-` sobald HTTPS genutzt wird. Zustandsändernde Anfragen brauchen den Header `X-CSRF-Token` (aus der Sitzung abgeleitet) und einen passenden `Origin`.
* Rollen (`owner`, `admin`, `editor`, `viewer`) stammen aus der Mitgliedschaft in unserer Datenbank, nicht aus Token-Claims. Ein unbekannter Nutzer ohne Einladung erhält keinen Zugang zu fremden Daten.
* Einladungen: einmaliger Token (nur Hash gespeichert), an eine normalisierte E-Mail-Adresse gebunden, mit Ablauf. Die Annahme setzt eine verifizierte E-Mail-Adresse des Providers voraus. Der letzte aktive Owner eines Mandanten kann nicht entfernt werden (Trigger in der Datenbank).
* Eine Drosselung von Anmeldeversuchen gibt es im Gateway nicht; Brute-Force-Schutz für Passwörter liefert Keycloak (siehe Grenzen). Öffentlicher und interner Issuer können sich unterscheiden (Container sprechen Keycloak intern an, der Browser extern); die Umschreibung ist in `identity/oidc.py` gekapselt und getestet.

## Verworfen
* JWT als Sitzungsersatz im Browser (Widerruf schwierig, Token im JavaScript erreichbar).
* Rollen aus Keycloak-Gruppen übernehmen: koppelt Fachrechte an die IdP-Konfiguration einzelner Kunden.

## Folgen
Sitzungen lassen sich einzeln widerrufen (Abmelden, Deaktivieren einer Mitgliedschaft wirkt sofort). Ein Wechsel des Identity Providers ändert nur die OIDC-Konfiguration. Echte Unternehmens-SSO-Anbindungen eines Kunden (SAML, SCIM, Gruppenmapping) sind nicht umgesetzt.
