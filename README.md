# read-log

読んだ技術記事の記録。`site/` が GitHub Pages で公開される静的サイトで、データは `site/data/articles.json` に入っている。

## 記事を追加する

Claude Code で記事の URL を送る。スキル `read-log`（`.claude/skills/read-log`）が、取得、カテゴリ・タグ・要点の付与、重複確認、追加、アーカイブの依頼、コミット、push まで行う。

手で追加するときは、JSON を標準入力で渡す。

```sh
python scripts/readlog.py add <<'EOF'
{"url": "https://example.com/post", "title": "タイトル", "category": "infra", "tags": ["kubernetes"], "summary": "記事が扱う内容を1文で", "points": ["要点1", "要点2"]}
EOF
```

| コマンド | 内容 |
|---|---|
| `fetch URL [--chars N]` | ページのタイトル、公開日、本文の冒頭（既定1200字）を JSON で出す |
| `find 題名` | 題名から URL の候補を探す。はてなブックマーク（サイトを問わない）、note、Qiita の検索を引く。全角半角、空白、括弧、末尾の「～」などの表記ゆれは自動で吸収し、題名が一致した候補に `match` を付ける |
| `add [--update]` | 記事を追加する（配列で複数件）。重複は追加されない |
| `edit` | 登録済みの記事の項目を直す。`id` か `url` と、変えたい項目だけを JSON で渡す |
| `tags` | 使用中のカテゴリとタグを件数つきで出す |
| `categories` | カテゴリ id の一覧 |
| `remove URL_OR_ID` | 記事を削除する |
| `archive [URL_OR_ID ...]` | Wayback Machine に保存を依頼し、成功した記事に `archived` を記録する。引数なしでは未保存の記事すべて |
| `check-links` | 各記事の URL に問い合わせ、404 と 410 を返すものを出す。403、429、5xx、タイムアウトは警告にとどめる |
| `normalize` | `articles.json` を正規の形に書き直す（項目の追加後に1回） |
| `validate` | データの整合性を確かめる（CI でも実行） |

## データ

`site/data/articles.json`（読んだ日の新しい順）。

| 項目 | 内容 |
|---|---|
| `id` | URL から作る8桁。`http` / `https`、`www`、末尾スラッシュ、`utm_*` の違いは同じ記事として扱う |
| `url`, `title`, `site`, `author` | 記事の情報。`site` はドメイン |
| `published`, `read` | 公開日、読んだ日（`YYYY-MM-DD`） |
| `category` | `site/data/categories.json` の id から1つ |
| `kind` | `article` / `slide` / `video` / `paper` / `book` / `tool` / `page`。ドメインから推定し、書籍、PDF の冊子、ツールやアプリ（`tool`）、参考サイトやアドベントカレンダーのような一覧ページ（`page`）は明示する |
| `tags` | 1〜8個。小文字、空白はハイフン。`site/data/tag_aliases.json` の別名は置き換わる |
| `summary` | 記事が扱う内容を事実で1文 |
| `points` | 本文から読み取った事実を3つまで（1つ80字以内）。検索の対象になる |
| `lang` | `ja` / `en` など |
| `archived` | Wayback Machine に保存を依頼した日。未保存は `null` |

## 検索

| 書き方 | 意味 |
|---|---|
| `語1 語2` | すべてを含む。題名、要約、要点、タグ、サイト、著者が対象。かな、全角半角、大文字小文字は区別しない。4文字以上の英単語は1〜2文字の打ち間違いを許す |
| `"a b"` | 語句として一致 |
| `-語` | 含まない |
| `tag:k8s` | タグ。複数並べると AND、`tag:a,b` は OR |
| `cat:cloud,infra` | カテゴリ（id または表示名）。複数は OR |
| `kind:slide` | 形式（id または表示名）。`kind:スライド`、`kind:slide,book` |
| `site:zenn` | ドメインの部分一致 |
| `lang:en` | 言語 |
| `read:2026-03` / `pub:2025` | 読んだ日、公開日の前方一致 |
| `-tag:x` `-cat:x` `-kind:x` `-site:x` | 除外 |

操作は URL のハッシュに入るので、検索結果はそのままリンクとして共有できる。`/` で検索欄に移動する。要点にだけ含まれる語で見つかった記事は、その要点がカードに出る。

タグ欄は、2件以上の記事に付いたタグを最初に表示し、残りは「+N」で開く。

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

`.github/workflows/linkcheck.yml` が毎月1日に `check-links` を実行する。404 か 410 の記事があると失敗し、通知が届く。記事は残り、サイトの `ARCHIVE` リンクから保存済みの写しを読める。

見た目は `DESIGN.md` に従う。
