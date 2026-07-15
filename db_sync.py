import os
import shutil
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKUP_BUCKET = "war-game-data"  # Supabase Storageに作成するバケット名
DB_FILE = os.getenv("DB_FILE", "war_game_worlds.db")

# Supabaseクライアント初期化
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def download_db():
    """起動時にSupabase StorageからDBファイルをダウンロードする"""
    if not supabase:
        print("[Sync] Supabaseの認証情報が設定されていません。ローカルDBを使用します。")
        return
    
    try:
        # バケットからDBファイルを取得
        print(f"[Sync] {DB_FILE} を Supabase からダウンロード中...")
        response = supabase.storage.from_(BUCKUP_BUCKET).download(DB_FILE)
        
        # ローカルに保存
        with open(DB_FILE, "wb") as f:
            f.write(response)
        print("[Sync] ダウンロード成功！ゲームデータを復元しました。")
    except Exception as e:
        print(f"[Sync] ダウンロード未完了（初回起動、またはファイル未存在の可能性があります）: {e}")

def upload_db():
    """ゲームのセーブ時や終了時にDBファイルをSupabase Storageへアップロードする"""
    if not supabase:
        return
    if not os.path.exists(DB_FILE):
        print(f"[Sync] {DB_FILE} が見つからないため、アップロードをスキップします。")
        return

    try:
        print(f"[Sync] {DB_FILE} を Supabase へアップロード中...")
        with open(DB_FILE, "rb") as f:
            # upsert=True で既存ファイルを上書き保存
            supabase.storage.from_(BUCKUP_BUCKET).upload(
                path=DB_FILE, 
                file=f, 
                file_options={"cache-control": "3600", "upsert": "true"}
            )
        print("[Sync] アップロード成功！データが永続化されました。")
    except Exception as e:
        print(f"[Sync] アップロード失敗: {e}")
