# read-log

読んだ技術記事を記録して GitHub Pages に出すリポジトリ。記事の追加はスキル `read-log` で行う。

- `site/data/articles.json` は手で編集しない。`scripts/readlog.py` 経由で追加・削除する（並び順、重複、タグの正規化を保つため）
- サイトの見た目は `DESIGN.md` に従う。影とグラデーションは使わない
- 画面に説明文や操作案内を足さない。見た目で伝える
- push は確認を取らずに行う（ユーザーの指示）。ただし public リポジトリなので、上がるファイルに個人情報や設定ファイルが入っていないことを先に確かめる
- テスト: `python -m unittest discover -s tests` と `node --test tests/lib.test.js`
