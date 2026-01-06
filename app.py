import streamlit as st
from google import genai  # ← ここが変わった！
from google.genai import types
from google.genai import errors # エラーハンドリング用
import tempfile
import os
from bs4 import BeautifulSoup
import time # リトライ時のwait用

# APIキー設定（Streamlitのsecretsか環境変数から）
# api_key = os.environ.get("GEMINI_API_KEY") 

st.title("決算書まとめBot（新SDK対応版）")

if "chat_session" not in st.session_state:
    st.session_state.chat_session = None
if "summary_done" not in st.session_state:
    st.session_state.summary_done = False
if "messages" not in st.session_state:
    st.session_state.messages = []

# クライアントの初期化
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"]) # キーは適切に設定

client = get_gemini_client()

uploaded_file = st.file_uploader("決算書(PDF or HTML)を添付してください。", type=["pdf", "htm", "html"])

if uploaded_file and not st.session_state.summary_done:
    with st.spinner("AIが解析中です..."):
        
        content_to_send = "" # 文字列またはファイルオブジェクトが入る
        
        # ファイル処理（ここはロジック同じ）
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()

        if file_ext == ".pdf":
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
        system_instruction = "あなたはプロの機関投資家です。ユーザーの質問には日本語で、数値に基づき正確に答えてください。HTMLタグが含まれている場合は、タグを無視して本文の内容を分析してください。出力した文の最後には必ず「※本システムはAIによる自動生成であり、情報の正確性を保証するものではありません。投資判断は自己責任でお願いします。」と書きなさい。"
        generation_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2
        )

        # モデル定義
        primary_model = "gemini-2.5-flash"
        fallback_model = "gemini-2.5-flash-lite"
        
        # 【変更点3】最初のメッセージ送信とFallbackループ
        # PDF(File object)とテキストを混ぜて送る場合
        prompt_text = "この資料の要点を、財務ハイライト、将来の見通し、懸念点の3つに分けて日本語で「プロの機関投資家」という文言を使わず、前置きなしでまとめてください。最初に会社名を示してください。その後、この資料の要点を以下の見出しで日本語でまとめて。その際、該当する内容が添付ファイル内にない場合はその章は「情報なし」と書くこと。ただし「劣位性」については記載がない場合は、リスク要因（Risk Factors）や財務状況から、競合他社と比較して弱点となり得る要素を推論して記述してください。以下は使用する見出しです。強調と改行をして表示させなさい。1.会社について,1-1.セグメント別事業内容,1-2.大型契約情報,1-3.競合他社に対する優位性,1-4.競合他社に対する劣位性,2.財務ハイライト,2-1.売上高,2-2.営業利益,2-3.株当たり利益,2-4.キャッシュフロー,2-5.賃借対照表,2-6.売上高の変動要因,3.将来の見通し,3-1.業績予想,3-2.事業拡大と政府支援,3-3.セグメント別成長期待,4.懸念点"
        
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
                        
                        # System instruction再定義（簡略化のため再記述または共通変数から取得が望ましいが、ここではハードコード回避のためsessionから取れればベストだがconfigは見えないことが多い）
                        # ※コード上部の変数スコープはこのifブロック内では見えない可能性があるため、再定義します
                        system_instruction_retry = "あなたはプロの機関投資家です。ユーザーの質問には日本語で、数値に基づき正確に答えてください。HTMLタグが含まれている場合は、タグを無視して本文の内容を分析してください。出力した文の最後には必ず「※本システムはAIによる自動生成であり、情報の正確性を保証するものではありません。投資判断は自己責任でお願いします。」と書きなさい。"
                        
                        new_chat = client.chats.create(
                            model=fallback_model,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction_retry,
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