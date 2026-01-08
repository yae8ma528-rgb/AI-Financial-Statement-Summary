import streamlit as st
import time
import os
import uuid
import prompts
import utils
import gemini_logic
import help
import update_history
from google.genai import errors

# ページ設定
st.set_page_config(
    page_title="決算書まとめBot",
    page_icon="🤖",
    layout="centered",
)

# 日本語設定ハック
utils.setup_japanese_language()

st.title("決算書まとめBot v0.3.3β")

# クライアント取得
client = gemini_logic.get_gemini_client()

if "current_page" not in st.session_state:
    st.session_state.current_page = "main"

# --- サイドバー: メニュー ---
with st.sidebar:
    st.header("メニュー")
    
    # ページ切り替えボタン
    if st.button("ホーム"):
        st.session_state.current_page = "main"
        st.rerun()

    if st.button("使い方"):
        st.session_state.current_page = "help"
        st.rerun()
        
    if st.button("更新履歴"):
        st.session_state.current_page = "history"
        st.rerun()

    st.divider()

    if st.button("分析をリセット"):
        st.session_state.confirm_reset = True

    if st.session_state.get("confirm_reset"):
        st.warning("本当にリセットしますか？\n会話履歴とアップロードしたファイルが削除されます。")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("削除"):
                # クラウド上のファイルを削除
                if st.session_state.get("uploaded_gemini_file_names"):
                    gemini_logic.delete_files_from_gemini(client, st.session_state.uploaded_gemini_file_names)
                    st.sidebar.success("クラウド上のファイルを全て削除しました")
                
                # セッション初期化 (current_pageは維持するか、mainに戻すか。ここではmainに戻す)
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                # 新しいuploader_keyを設定してリセット
                st.session_state.uploader_key = str(uuid.uuid4())
                st.session_state.current_page = "main"
                st.rerun()
        with col2:
            if st.button("キャンセル"):
                st.session_state.confirm_reset = False
                st.rerun()

# --- セッション状態の初期化 (削除後も再生成されるように配置) ---
# 他のsession state初期化は下にあるのでcurrent_pageだけここでも確認（リセット直後用）
if "current_page" not in st.session_state:
    st.session_state.current_page = "main"
if "chat_session" not in st.session_state:
    st.session_state.chat_session = None
if "summary_done" not in st.session_state:
    st.session_state.summary_done = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "uploaded_gemini_file_names" not in st.session_state:
    st.session_state.uploaded_gemini_file_names = []
if "analysis_mode" not in st.session_state:
    st.session_state.analysis_mode = None 
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = str(uuid.uuid4())

# コールバック関数
def set_analysis_mode(mode):
    st.session_state.analysis_mode = mode

# --- メインロジック ---

# --- メインロジック ---
if st.session_state.current_page == "main":
    uploaded_files = st.file_uploader(
        "決算書(PDF or HTML)を添付してください。", 
        type=["pdf", "htm", "html"], 
        key=st.session_state.uploader_key, 
        accept_multiple_files=True
    )

    if uploaded_files and not st.session_state.summary_done:
        
        # 処理対象の決定とプロンプト選択
        should_process = False
        target_prompt = None
        
        # 1ファイル: 自動実行
        if len(uploaded_files) == 1:
            should_process = True
            target_prompt = prompts.PROMPT_FINANCIAL_SUMMARY
        
        # 複数ファイル: モード選択待機
        else:
            # モード未選択時はボタン表示
            if st.session_state.analysis_mode is None:
                st.info(f"{len(uploaded_files)} 個のファイルが選択されました。実行する分析を選択してください。")
                col1, col2 = st.columns(2)
                col1.button("1社の長期トレンド分析", on_click=set_analysis_mode, args=("trend",))
                col2.button("複数社の比較", on_click=set_analysis_mode, args=("compare",))
            
            # モード選択済み
            elif st.session_state.analysis_mode:
                should_process = True
                if st.session_state.analysis_mode == "trend":
                    target_prompt = prompts.PROMPT_TREND_ANALYSIS
                elif st.session_state.analysis_mode == "compare":
                    target_prompt = prompts.PROMPT_COMPANY_COMPARISON

        # --- 分析実行フロー ---
        if should_process and target_prompt:
            with st.spinner("AIが解析中です..."):
                
                contents_to_send = []
                
                for u_file in uploaded_files:
                    # ユーティリティでファイルを処理
                    processed_data = utils.process_uploaded_file(u_file)
                    
                    if processed_data:
                        if processed_data["type"] == "pdf":
                            # PDFはアップロードが必要
                             try:
                                uploaded_gemini_file = gemini_logic.upload_file_to_gemini(
                                    client, 
                                    processed_data["content"], # ここはファイルパス
                                    processed_data["display_name"]
                                )
                                contents_to_send.append(uploaded_gemini_file)
                                st.session_state.uploaded_gemini_file_names.append(uploaded_gemini_file.name)
                             finally:
                                 # 一時ファイル削除
                                 if processed_data["tmp_path"] and os.path.exists(processed_data["tmp_path"]):
                                     os.remove(processed_data["tmp_path"])

                        elif processed_data["type"] == "html":
                            # HTMLテキストはヘッダーをつけて追加
                            clean_text = f"--- File: {processed_data['display_name']} ---\n{processed_data['content']}"
                            contents_to_send.append(clean_text)
                
                if contents_to_send:
                    # リトライループ (最大3回)
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            # API呼び出し (ストリーミング)
                            chat, response_stream, used_model = gemini_logic.send_message_stream_with_fallback(
                                client,
                                contents_to_send,
                                target_prompt,
                                prompts.SYSTEM_INSTRUCTION
                            )
                            
                            if chat and response_stream:
                                st.session_state.chat_session = chat
                                st.session_state.current_model = used_model
                                
                                # ストリーミング表示
                                with st.chat_message("assistant"):
                                    # st.write_stream はジェネレータを受け取り、完了後に全テキストを返す
                                    # gemini_logic側でテキスト抽出とクリーニングを行っているのでそのまま渡す
                                    full_response_text = st.write_stream(response_stream)
                                
                                # 履歴保存
                                st.session_state.messages.append({"role": "assistant", "content": full_response_text})
                                st.session_state.summary_done = True
                                st.rerun()
                                break # 成功したらループを抜ける
                            else:
                                if attempt == max_retries - 1:
                                    st.error("解析に失敗しました。")
                        
                        except (errors.ClientError, errors.ServerError) as e:
                            if attempt < max_retries - 1:
                                st.warning(f"通信エラーが発生しました。再試行します... ({attempt+1}/{max_retries})")
                                time.sleep(2)
                            else:
                                st.error(f"エラーにより解析を完了できませんでした: {e}")

    # --- チャットインターフェース ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("他に聞きたいことは？"):
        if not st.session_state.chat_session:
            st.toast("先にファイルをアップロードして分析を行ってください。", icon="⚠️")
        else:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.spinner("思考中..."):
                try:
                    current_chat = st.session_state.chat_session
                    max_retries = 3
                    
                    for attempt in range(max_retries):
                        try:
                            response_stream = None
                            
                            # 1回目は既存のセッションで試行
                            if attempt == 0:
                                response_stream = current_chat.send_message_stream(prompt)
                                # 生のイテレータをクリーニング用ジェネレータでラップ
                                clean_stream = gemini_logic.clean_stream_generator(response_stream)
                            
                            # 2回目以降（リトライ）は新しいセッションを作り直す
                            else:
                                st.warning(f"混雑しています。バックアップ回線で再接続中... ({attempt}/{max_retries-1})")
                                time.sleep(2)
                                
                                old_history = current_chat.history
                                new_chat, new_stream, used_model = gemini_logic.send_message_stream_with_fallback(
                                    client,
                                    content=[], 
                                    prompt=prompt,
                                    system_instruction=prompts.SYSTEM_INSTRUCTION,
                                    previous_history=old_history
                                )
                                
                                if new_chat and new_stream:
                                    st.session_state.chat_session = new_chat
                                    st.session_state.current_model = used_model
                                    current_chat = new_chat # ループ内での参照用
                                    clean_stream = new_stream # これは既にクリーニング済み（fallback関数戻り値）
                                else:
                                    raise Exception("Retry session creation failed")

                            with st.chat_message("assistant"):
                                full_response_text = st.write_stream(clean_stream)
                            
                            st.session_state.messages.append({"role": "assistant", "content": full_response_text})
                            break # 成功終了

                        except (errors.ClientError, errors.ServerError) as e:
                            # 429/503はリトライ対象
                            if e.code in [429, 503] or "429" in str(e) or "503" in str(e):
                                if attempt == max_retries - 1:
                                    st.error(f"申し訳ありません、サーバーが大変混雑しており応答できませんでした。しばらく待ってから再度お試しください。({e})")
                            else:
                                # その他のAPIエラーは即終了
                                st.error(f"APIエラー: {e}")
                                break
                                
                        except Exception as e:
                             if attempt == max_retries - 1:
                                st.error(f"予期せぬエラー: {e}")
                             # それ以外はリトライ続行

                except Exception as e:
                    st.error(f"システムエラー: {e}")

elif st.session_state.current_page == "help":
    st.header("使い方")
    st.markdown(help.HELP_MARKDOWN)

elif st.session_state.current_page == "history":
    st.header("更新履歴")

    st.markdown(update_history.UPDATE_HISTORY_MARKDOWN)
