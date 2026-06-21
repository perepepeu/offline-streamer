# 🎬 Offline Streamer

Um mini YouTube local rodando no seu PC. Baixe vídeos e playlists do YouTube com `yt-dlp` e assista numa interface moderna no navegador.

## ✨ Features

- 📁 Playlists detectadas e exibidas separadamente dos vídeos soltos
- 🎨 Interface estilo YouTube (dark mode, cards, thumbnails)
- 🔍 Busca local por título e canal
- 📌 Favoritos persistidos em `favorites.json`
- 🕓 Histórico de reprodução (último assistido primeiro)
- 📊 Progresso de download em tempo real via SSE
- ⏭️ Player com autoplay e fila automática da playlist
- 🏷️ Tags/categorias por vídeo
- 🗑️ Remoção de vídeos com confirmação
- 📤 Exportar metadados da biblioteca em JSON/CSV
- ⚙️ Painel de configurações (pasta de download, qualidade padrão)

## 🚀 Instalação

```bash
pip install flask yt-dlp
# Instale também o ffmpeg e adicione ao PATH
python app.py
```

Abra `http://127.0.0.1:5000`

## 📁 Estrutura

```
offline-streamer/
├── app.py              # Backend Flask
├── config.json         # Configurações do usuário
├── favorites.json      # Favoritos
├── history.json        # Histórico de reprodução
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── watch.html
│   ├── playlist.html
│   └── settings.html
└── static/
    ├── style.css
    └── app.js
```
