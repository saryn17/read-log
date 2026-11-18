# read-log

読んだ技術記事の記録。`site/` が GitHub Pages で公開される静的サイトで、データは `site/data/articles.json` に入っている。

## 記事を追加する

Claude Code で記事の URL を送る。スキル `read-log`（`.claude/skills/read-log`）が、取得、カテゴリとタグ付け、重複確認、追加、コミットまで行う。

手で追加するときは、JSON を標準入力で渡す。

```sh
python scripts/readlog.py add <<'EOF'
{"url": "https://example.com/post", "title": "タイトル", "category": "infra", "tags": ["kubernetes"], "summary": "記事が扱う内容を1文で"}
EOF
```

| コマンド | 内容 |
|---|---|
| `fetch URL` | ページのタイトル、公開日、本文の冒頭を JSON で出す |
| `add [--update]` | 記事を追加する（配列で複数件）。重複は追加されない |
| `tags` | 使用中のカテゴリとタグを件数つきで出す |
| `categories` | カテゴリ id の一覧 |
| `remove URL_OR_ID` | 記事を削除する |
| `validate` | データの整合性を確かめる（CI でも実行） |

## データ

`site/data/articles.json`（読んだ日の新しい順）。

| 項目 | 内容 |
|---|---|
| `id` | URL から作る8桁。`http` / `https`、`www`、末尾スラッシュ、`utm_*` の違いは同じ記事として扱う |
| `url`, `title`, `site`, `author` | 記事の情報。`site` はドメイン |
| `published`, `read` | 公開日、読んだ日（`YYYY-MM-DD`） |
| `category` | `site/data/categories.json` の id から1つ |
| `tags` | 1〜8個。小文字、空白はハイフン。`site/data/tag_aliases.json` の別名は置き換わる |
| `summary` | 記事が扱う内容を事実で1文 |
| `lang` | `ja` / `en` など |

## 検索

| 書き方 | 意味 |
|---|---|
| `語1 語2` | すべてを含む。かな、全角半角、大文字小文字は区別しない。4文字以上の英単語は1〜2文字の打ち間違いを許す |
| `"a b"` | 語句として一致 |
| `-語` | 含まない |
| `tag:k8s` | タグ。複数並べると AND、`tag:a,b` は OR |
| `cat:cloud,infra` | カテゴリ（id または表示名）。複数は OR |
| `site:zenn` | ドメインの部分一致 |
| `lang:en` | 言語 |
| `read:2026-03` / `pub:2025` | 読んだ日、公開日の前方一致 |
| `-tag:x` `-cat:x` `-site:x` | 除外 |

操作は URL のハッシュに入るので、検索結果はそのままリンクとして共有できる。`/` で検索欄に移動する。

## ローカルで見る

```sh
python -m http.server 8000 --directory site
```

`http://localhost:8000` を開く。`index.html` をファイルとして直接開くと、ブラウザがデータの読み込みを拒否する。

## テスト

```sh
python -m unittest discover -s tests
node --test tests/lib.test.js
```

## 公開

`main` への push で `.github/workflows/pages.yml` が動く。`validate` とテストが通ると、`site/` だけが公開される。

見た目は `DESIGN.md` に従う。
