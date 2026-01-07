import streamlit as st
import time
from google import genai
from google.genai import types, errors

# クライアントの初期化
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

def upload_file_to_gemini(client, file_path, display_name):
    """Geminiにファイルをアップロードする"""
    try:
        uploaded_file = client.files.upload(
            file=file_path, 
            config={'display_name': display_name}
        )
        return uploaded_file
    except Exception as e:
        raise e

def delete_files_from_gemini(client, file_names):
    """Gemini上のファイルを削除する"""
    for fname in file_names:
        try:
            client.files.delete(name=fname)
        except Exception as e:
            # 個別の削除エラーはログに出すか無視して続行
            print(f"Failed to delete {fname}: {e}")

def create_chat_session(client, model, system_instruction, history=[]):
    """チャットセッションを作成する"""
    generation_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.2
    )
    return client.chats.create(
        model=model,
        config=generation_config,
        history=history
    )

def clean_stream_generator(stream):
    """
    ストリームからテキストを抽出し、エスケープされた改行文字を修正してyieldするジェネレータ
    """
    for chunk in stream:
        if chunk.text:
            yield chunk.text.replace("\\n", "\n")



def send_message_stream_with_fallback(client, content, prompt, system_instruction, previous_history=[]):
    """
    メッセージをストリーミング送信し、429エラーが発生した場合はFallbackモデルで再試行する。
    
    Returns:
        tuple: (chat_session, response_stream, used_model)
    """
    primary_model = "gemini-2.5-flash"
    fallback_model = "gemini-2.5-flash-lite"
    models_to_try = [primary_model, fallback_model]
    
    if isinstance(content, list):
        message_payload = content + [prompt] if prompt else content
    else:
        message_payload = [content, prompt] if prompt else [content]
        
    active_chat = None
    response_stream = None
    used_model = ""

    for model_name in models_to_try:
        try:
            chat = create_chat_session(client, model_name, system_instruction, previous_history)
            
            # send_message_stream はジェネレータを返すが、実際のリクエスト開始やエラー発生は
            # 最初の要素を取得する時まで遅延する場合がある。
            # そのため、ここで最初のchunkを取得してみることで、429エラーをこのtryブロック内で確実に捕捉する。
            stream = chat.send_message_stream(message_payload)
            
            # イテレータ化して最初の要素を取得
            iterator = iter(stream)
            try:
                first_chunk = next(iterator)
            except StopIteration:
                # 空のレスポンスの場合
                first_chunk = None

            # 成功したら、最初のchunkと残りのiteratorを結合して新しいジェネレータを作る
            def reconstructed_stream():
                if first_chunk:
                    yield first_chunk
                yield from iterator

            active_chat = chat
            # clean_stream_generator には結合したジェネレータを渡す
            response_stream = clean_stream_generator(reconstructed_stream())
            used_model = model_name
            break

        except (errors.ClientError, errors.ServerError) as e:
            if e.code in [429, 503] or "429" in str(e) or "503" in str(e):
                st.warning(f"モデル {model_name} が混雑しています(429/503)。次のモデルに切り替えます...")
                time.sleep(1)
                continue
            else:
                st.error(f"APIエラーが発生しました: {e}")
                break
        except Exception as e:
            st.error(f"予期せぬエラーが発生しました: {e}")
            break
            
    return active_chat, response_stream, used_model
