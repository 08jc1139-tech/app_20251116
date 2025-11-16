# 休暇・勤怠申請デモアプリ

Python の最小 HTTP サーバ `app/main.py` とシングルページ UI `app/ui.html` で構成されたデモです。`python app/main.py` を実行してブラウザからアクセスすると、休暇申請・勤怠修正申請・承認・レポート・管理設定を確認できます。

## 構成

```
my-app/
  ├─ app/
  │   ├─ main.py   # Web サーバ + API エンドポイント
  │   └─ ui.html   # SPA フロントエンド（fetch で /api/* を呼び出す）
  └─ README.md
```

- ルート `/` で `ui.html` を配信し、同一ホスト上の `/api/*` で JSON API を提供します。
- UI と API はセットで更新してください（Excel 互換 CSV 項目や承認フローをずらさないため）。

## 起動方法（ローカル）

```
python app/main.py
# 出力例: http://localhost:8000/
```

ブラウザで `http://localhost:8000/` を開き、簡易ログインを選択して利用します。サーバーは `app/data.json` にデータを保存する簡易実装です（初回起動時にシードデータを生成）。

## GitHub 上での利用/公開例

- **Codespaces**: Codespaces でリポジトリを開き、ターミナルで `python app/main.py` を実行。ポート 8000 を公開すると同じ UI/ルーティングで利用できます。
- **GitHub Pages**: Pages は静的配信専用のため、`ui.html` 単体では API を呼べません。Pages でフロントを配信する場合は、同一リポジトリをサーバー実行できるホスト（例: Codespaces、Render、Fly.io など）にデプロイし、UI からそのホストの `/api/*` にアクセスさせてください。
- **任意の PaaS/VPS**: リポジトリを配置し `python app/main.py` または `gunicorn app.main:app` スタイルで起動するだけで動作します。`PORT` 環境変数があればそれを利用します。

## 主な API（サーバー: `main.py`）

すべての状態変更系は POST。認可用にヘッダー `X-User-Id`, `X-User-Role` を付与します。

- `POST /api/login` … `{user_id}` を受け取りユーザー情報を返す（簡易ログイン）
- `GET /api/meta` … ユーザー・休暇種別・休日・承認経路
- `GET /api/leave_requests?scope=mine|team` … 休暇申請一覧（社員: 自分のみ、上長: scope=team で部下含む、管理者: 全件）
- `POST /api/leave_requests` … 休暇申請登録（`start_date`, `end_date`, `leave_type`, `reason`）
- `GET /api/attendance_corrections?scope=mine|team` … 勤怠修正一覧
- `POST /api/attendance_corrections` … 勤怠修正申請（`date`, `clock_in`, `clock_out`, `reason` 等）
- `POST /api/approvals` … 上長/管理者による承認・却下 `{category:"leave|correction", id, action:"approved|rejected", comment}`
- `GET /api/reports` … 承認済みデータの集計（期間/部門/社員でフィルタ）
- `GET /api/reports/export` … 上記フィルタを反映した Excel 互換 CSV
- `POST /api/settings` … 管理者向け設定（休暇種別、休日、承認経路）

### バリデーションとエラーハンドリング

- サーバー/クライアント両方で必須項目と日付範囲を確認。不正な場合は `{ok:false, message, fields?}` を返します。
- 予期しない例外時は HTTP 500 + 汎用メッセージ（スタックトレースは標準出力にのみ記録）。
- 状態変更系は POST のみ。将来的に CSRF トークンを追加しやすいヘッダー構造にしています。

## フロントエンド（`ui.html`）

- fetch API で同一ホストの `/api/*` を呼び出す SPA。ページリロードなしで申請・承認・集計ができます。
- ロールに応じて表示領域が切り替わります（社員/上長/管理者）。
- CSV エクスポートはブラウザでダウンロードリンクを自動生成します。

## データモデル拡張のヒント

- `app/data.json` の構造（leave_requests, attendance_corrections, approval_routes など）を増やす場合は、必ず `ui.html` の表示項目と `main.py` の API を同時に更新してください。
- Excel 運用のカラム追加や承認ステップ増加も同様にフロント/バック双方で整合を取る前提です。
