import streamlit as st
import streamlit.components.v1 as components # Javascript注入用
from google import genai  # ← ここが変わった！
from google.genai import types
from google.genai import errors # エラーハンドリング用
import tempfile
import os
from bs4 import BeautifulSoup
import time # リトライ時のwait用
import prompts # プロンプト定義ファイル
import uuid # リセット時のキー生成用

# APIキー設定（Streamlitのsecretsか環境変数から）
# api_key = os.environ.get("GEMINI_API_KEY") 

st.set_page_config(
    page_title="決算書まとめBot",
    page_icon="🤖",
    layout="centered",
)

# ブラウザに日本語サイトとして認識させるためのJavascriptハック
# st.markdownではスクリプトが実行されない場合があるためcomponentsを使用
components.html("""
    <script>
        window.parent.document.getElementsByTagName('html')[0].lang = 'ja';
    </script>
""", height=0) 

st.title("決算書まとめBot v0.2.2β")

# サイドバー: リセット機能
with st.sidebar:
    st.header("メニュー")
    if st.button("分析をリセット"):
        st.session_state.confirm_reset = True

    if st.session_state.get("confirm_reset"):
        st.warning("本当にリセットしますか？\n会話履歴とアップロードしたファイルが削除されます。")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("削除"):
                # Gemini上のファイルを削除
                if st.session_state.get("uploaded_gemini_file_name"):
                    try:
                        client = get_gemini_client()
                        client.files.delete(name=st.session_state.uploaded_gemini_file_name)
                        st.sidebar.success("クラウド上のファイルを削除しました")
                    except Exception as e:
                        st.sidebar.error(f"ファイル削除エラー: {e}")
                
                # セッション初期化
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                # 新しいuploader_keyを設定してリセット
                st.session_state.uploader_key = str(uuid.uuid4())
                st.rerun()
        with col2:
            if st.button("キャンセル"):
                st.session_state.confirm_reset = False
                st.rerun()

if "chat_session" not in st.session_state:
    st.session_state.chat_session = None
if "summary_done" not in st.session_state:
    st.session_state.summary_done = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "uploaded_gemini_file_name" not in st.session_state:
    st.session_state.uploaded_gemini_file_name = None
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = str(uuid.uuid4())

# クライアントの初期化
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"]) # キーは適切に設定

client = get_gemini_client()

uploaded_file = st.file_uploader("決算書(PDF or HTML)を添付してください。", type=["pdf", "htm", "html"], key=st.session_state.uploader_key)

if uploaded_file and not st.session_state.summary_done:
    with st.spinner("AIが解析中です..."):
        
        content_to_send = "" # 文字列またはファイルオブジェクトが入る
        
        # ファイル処理（ここはロジック同じ）
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()

        if file_ext == ".pdf":
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_path = tmp_file.name
                
                # 【変更点1】ファイルアップロード
                # config引数でdisplay_nameを指定する
                uploaded_gemini_file = client.files.upload(
                    file=tmp_path, 
                    config={'display_name': 'Earnings Report PDF'}
                )
                content_to_send = uploaded_gemini_file
                # リセット用にファイル名を保存
                st.session_state.uploaded_gemini_file_name = uploaded_gemini_file.name
            finally:
                # 確実にファイルを削除する
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path) 

        elif file_ext in [".htm", ".html"]:
            # HTML処理（BeautifulSoup部分はそのまま）
            try:
                html_content = uploaded_file.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                html_content = uploaded_file.getvalue().decode("cp932")
            
            soup = BeautifulSoup(html_content, 'html.parser')
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()
            text_data = soup.get_text(separator="\n") 
            lines = [line.strip() for line in text_data.splitlines() if line.strip()]
            clean_text = "\n".join(lines)
            
            content_to_send = clean_text

        # 【変更点2】チャット開始 & エラーハンドリング/Fallback実装
        # 設定を共通化
        # system_instruction は prompts.py から読み込む
        generation_config = types.GenerateContentConfig(
            system_instruction=prompts.SYSTEM_INSTRUCTION,
            temperature=0.2
        )

        # モデル定義
        primary_model = "gemini-2.5-flash"
        fallback_model = "gemini-2.5-flash-lite"
        
        # 【変更点3】最初のメッセージ送信とFallbackループ
        # PDF(File object)とテキストを混ぜて送る場合
        prompt_text = prompts.PROMPT_FINANCIAL_SUMMARY
        
        models_to_try = [primary_model, fallback_model]
        active_chat = None
        response_text = ""

        for model_name in models_to_try:
            try:
                # Chatセッション作成
                chat = client.chats.create(
                    model=model_name,
                    config=generation_config,
                    history=[]
                )
                
                # 送信
                response = chat.send_message([content_to_send, prompt_text])
                
                # 成功したらループを抜ける
                active_chat = chat
                response_text = response.text
                st.session_state.current_model = model_name # 現在のモデルを保存
                break

            except errors.ClientError as e:
                # 429 Resource Exhausted (Rate Limit) の場合
                if e.code == 429 or "429" in str(e):
                    st.warning(f"モデル {model_name} が混雑しています(429)。次のモデルに切り替えます...")
                    time.sleep(1) # 一呼吸置く
                    continue
                else:
                    st.error(f"APIエラーが発生しました: {e}")
                    break
            except Exception as e:
                st.error(f"予期せぬエラーが発生しました: {e}")
                break
        
        if active_chat:
            st.session_state.chat_session = active_chat
            st.session_state.messages.append({"role": "assistant", "content": response_text})
            st.session_state.summary_done = True
        else:
            st.error("すべてのモデルでの解析に失敗しました。しばらく待ってから再試行してください。")

# 表示部分は変更なし
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("他に聞きたいことは？"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if st.session_state.chat_session:
        with st.spinner("思考中..."):
            try:
                # 既存のセッションでトライ
                response = st.session_state.chat_session.send_message(prompt)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                with st.chat_message("assistant"):
                    st.markdown(response.text)

            except errors.ClientError as e:
                # 429発生時、かつ今のモデルがprimaryならfallbackへ移行
                is_rate_limit = e.code == 429 or "429" in str(e)
                current_model = st.session_state.get("current_model", "gemini-2.5-flash")
                fallback_model = "gemini-2.5-flash-lite"
                
                if is_rate_limit and current_model != fallback_model:
                    st.warning(f"モデル {current_model} が混雑しています。{fallback_model} に切り替えて再試行します...")
                    
                    try:
                        # 履歴を引き継いで新しいチャットセッションを作成
                        # 注意: 古いhistoryには前回のやり取りが含まれている
                        old_history = st.session_state.chat_session.history
                        
                        # System instruction再定義（prompts.pyから参照）
                        
                        new_chat = client.chats.create(
                            model=fallback_model,
                            config=types.GenerateContentConfig(
                                system_instruction=prompts.SYSTEM_INSTRUCTION,
                                temperature=0.2
                            ),
                            history=old_history
                        )
                        
                        # 再送信
                        response = new_chat.send_message(prompt)
                        
                        # セッション更新
                        st.session_state.chat_session = new_chat
                        st.session_state.current_model = fallback_model
                        
                        st.session_state.messages.append({"role": "assistant", "content": response.text})
                        with st.chat_message("assistant"):
                            st.markdown(response.text)
                            
                    except Exception as retry_e:
                        st.error(f"再試行も失敗しました: {retry_e}")
                else:
                    st.error(f"エラーが発生しました: {e}")

            except Exception as e:
                st.error(f"予期せぬエラーが発生しました: {e}")
