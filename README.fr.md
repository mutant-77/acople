<p align="center">
  <img src="img/header.png" alt="Header">
</p>

<h1 align="center">Acople</h1>

<p align="center">
  Transformez les agents d'IA de terminal (comme Claude ou Gemini) en une API locale pour vos propres applications.
</p>

Languages: [Español](README.es.md) | [English](README.md) | [Français](README.fr.md)

---

## Pour qui est-ce ? 👀

Pour **vous** qui :
- Voulez utiliser Claude Code, Gemini, OpenCode, ou tout autre agent depuis votre application.
- Ne voulez pas vous casser la tête avec des configurations complexes.
- Voulez quelque chose qui **fonctionne**, tout simplement.

---

## Pourquoi Acople ? 🚀

### Agent vs Modèle
Acople ne se contente pas d'appeler une API de texte ; il appelle un **Agent** avec des "mains" (outils, navigation web, accès au système de fichiers). Alors qu'une API LLM n'est qu'un "cerveau" dans un bac à sable, Acople donne à ce cerveau la capacité d'agir sur votre environnement local.

### Pont & Normalisation
Arrêtez de vous battre avec différents drapeaux CLI et des formats de sortie incohérents. Acople offre une **interface unifiée** pour Claude Code, Gemini et les autres. Un seul format pour les gouverner tous.

### Votre terminal en tant qu'API
Comme vous ne pouvez pas exécuter de commandes CLI depuis un navigateur web ou une application mobile, le composant serveur d'Acople agit comme un **pont sécurisé**, exposant vos agents locaux via HTTP/SSE.

### Streaming en Temps Réel
Acople gère l'analyse complexe des flux de terminaux, vous donnant des jetons (tokens) propres en temps réel. C'est la différence entre une application figée et une expérience interactive.

### Prêt pour la Production
- Contrôle de **concurrence intégré**, gestion du cycle de vie des processus et authentification par clé API.
- **Compatible OpenAI :** Expose vos agents via une API compatible OpenAI (`/v1/chat/completions`), vous permettant d'utiliser des agents CLI avec n'importe quel outil d'IA.

---

## Démarrage Rapide ⚡ (en 30 secondes)

```bash
# 1. Installez
pip install acople[server]

# 2. Démarrez le serveur
uvicorn acople.server:app --port 8000
```

C'est prêt ! Vous pouvez maintenant utiliser l'agent depuis votre application.

---

## Utilisation basique 📦

```python
from acople import Acople

# Détecte automatiquement votre agent - aucune configuration nécessaire
bridge = Acople()

# Envoyez un prompt et recevez la réponse
async for event in bridge.run("Bonjour, qui es-tu ?"):
    print(event.data.get("text"), end="")
```

Ou si vous préférez utiliser le serveur HTTP :

```bash
# La méthode la plus simple
curl -X POST http://localhost:8000/chat/simple \
  -H "Content-Type: application/json" \
  -d '{"prompt": "crée un hello world en python"}'
```

---

## Sécurité et Concurrence 🛡️ (NOUVEAU)

Acople est maintenant prêt pour la production. Vous pouvez configurer ces variables d'environnement :

- `ACOPLE_API_KEY` : Définissez une clé secrète pour protéger vos endpoints (ex. `export ACOPLE_API_KEY="mon_secret"`). Puis passez-la dans l'en-tête `X-API-Key`.
- `ACOPLE_MAX_CONCURRENT` : Limite de sessions simultanées pour ne pas saturer votre ordinateur (par défaut `5`).
- `ACOPLE_CORS_ORIGINS` : Contrôlez qui peut accéder à votre API (par défaut `http://localhost:*`).
- `OPENAI_API_KEY` : Requise pour la génération d'images avec `gpt-image-1`.

---

## API Endpoints 🌐

| Endpoint | Ce qu'il fait | Quand l'utiliser |
|----------|---------|--------------|
| `POST /chat/simple` | Passez juste le prompt | Pour quelque chose de rapide et facile ✅ |
| `POST /chat` | Avec plus d'options | Quand vous avez besoin de plus de contrôle (ex. cwd, timeouts) |
| `POST /image/generate` | Génère des images | Génération d'images avec gpt-image-1 🎨 |
| `GET /agents` | Liste les agents installés | Pour voir ce qui est disponible |
| `GET /models` | Liste les modèles de l'agent | Pour choisir un modèle spécifique |
| `GET /health` | Le serveur est-il en vie ? | Vérification rapide de l'état |
| `GET /ui` | Interface web intégrée | Pour tester vos agents directement depuis le navigateur 🖥️ |
| `POST /interrupt` | Annule ce qui est en cours d'exécution | Pour arrêter une session ou toutes |
| `POST /v1/chat/completions` | Chat compatible OpenAI | Utilisez Acople comme backend pour n'importe quel outil d'IA 🔌 |
| `GET /v1/models` | Liste des modèles OpenAI | Compatibilité avec la spécification OpenAI |

---

## Interface de Test Intégrée 🖥️

Acople est livré avec une interface web moderne prête à l'emploi pour tester vos agents, vérifier l'état de la connexion et exécuter des diagnostics.

Démarrez simplement votre serveur :
```bash
uvicorn acople.server:app --port 8000
```
Et ouvrez dans votre navigateur : **`http://localhost:8000/ui`**

---

## Compatibilité OpenAI 🔌

Acople peut agir comme une **passerelle locale compatible OpenAI**. Cela signifie que vous pouvez pointer n'importe quel outil prenant en charge OpenAI (comme [NullClaw](https://github.com/nullclaw/nullclaw), *Continue*, *Cursor*, etc.) vers votre serveur local Acople.

**Configuration pour vos outils :**
- **Base URL :** `http://localhost:8000/v1`
- **API Key :** `n'importe-quelle-chaîne` (ou votre `ACOPLE_API_KEY`)
- **Model :** `acople/claude`, `acople/gemini`, etc.

Vos agents CLI préférés sont désormais disponibles sous forme d'API standard !

---

## Vous n'avez pas d'agent installé ? 🤔

Pas de soucis, installez-en un simplement :

```bash
# Choisissez celui que vous voulez :

# Claude Code (le plus populaire)
npm i -g @anthropic-ai/claude-code

# Gemini CLI (gratuit)
npm i -g @google/gemini-cli

# OpenCode (open source)
npm i -g opencode

# Kilo (fork d'OpenCode)
npm i -g kilo

# Codex CLI
npm i -g @openai/codex
```

---

## Vérifiez que tout va bien 🛠️

```bash
python -m acople.cli doctor
```

Il vous dit si tout est installé et fonctionnel.

---

## Erreurs utiles 💪

Si quelque chose échoue, Acople vous dit **exactement quoi faire** :

```text
# Avant (générique et confus)
Error: "L'agent n'est pas dans PATH"

# Après (clair)
Error: Claude n'est pas installé
→ Exécutez: npm i -g @anthropic-ai/claude-code
```

---

## Ce que vous pouvez faire 🎯

Avec Acople, vous pouvez créer :

- Votre propre **assistant de codage** personnel
- **Revue de code** automatique
- Générateur de **tests**
- **Débogueur** intelligent
- Tout ce qui **vous passe par la tête** ✨

---

## Génération d'Images 🎨

Acople peut générer des images en utilisant `gpt-image-1` d'OpenAI :

```bash
# Configurez votre clé API OpenAI
export OPENAI_API_KEY="sk-..."

# Générez une image
curl -X POST http://localhost:8000/image/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Une ville futuriste au coucher du soleil", "size": "1024x1024", "quality": "high"}'
```

Ou utilisez-le depuis Python :

```python
from acople import ImageBridge, ImageConfig

bridge = ImageBridge()
results = await bridge.generate(
    "Une ville futuriste au coucher du soleil",
    ImageConfig(size="1024x1024", quality="high")
)
# results[0].b64_data contient l'image encodée en base64
```

---

## Prérequis 📋

- Python 3.10+
- Au moins 1 agent CLI installé (parmi : `claude`, `gemini`, `opencode`, `kilo`, `codex`, `qwen`)

---

## Exemple complet 💻

```python
# client.py - votre application qui utilise l'agent
import httpx
import json

def chat(prompt):
    with httpx.Client() as client:
        # N'oubliez pas de passer la clé API si vous l'avez configurée !
        headers = {"X-API-Key": "mon_secret"} 
        with client.stream("POST", "http://localhost:8000/chat", json={"prompt": prompt}, headers=headers) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event["type"] == "token":
                        print(event.get("text", ""), end="")

# Utilisez-le !
chat("crée un bouton en HTML qui dit 'Clique-moi'")
```

---

MIT License
