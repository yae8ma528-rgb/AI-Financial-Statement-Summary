import streamlit as st
import streamlit.components.v1 as components
import os
import tempfile
from bs4 import BeautifulSoup

def setup_japanese_language():
    """ブラウザに日本語サイトとして認識させるためのJavascriptを注入"""
    components.html("""
        <script>
            window.parent.document.getElementsByTagName('html')[0].lang = 'ja';
        </script>
    """, height=0)

def process_uploaded_file(uploaded_file):
    """
    アップロードされたファイルを処理し、Geminiに送れる形式またはテキストを返す。
    
    Args:
        uploaded_file: StreamlitのUploadedFileオブジェクト
        
    Returns:
        dict: {
            "type": "pdf" or "html" or "text",
            "content": content_to_send,
            "tmp_path": temporary_file_path (for PDF cleanup, optional),
            "display_name": filename
        }
    """
    file_ext = os.path.splitext(uploaded_file.name)[1].lower()
    
    if file_ext == ".pdf":
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name
            
            return {
                "type": "pdf",
                "content": tmp_path, # Geminiへのアップロードはこのパスを使用
                "tmp_path": tmp_path,
                "display_name": uploaded_file.name
            }
        except Exception as e:
            st.error(f"PDF処理エラー: {e}")
            return None

    elif file_ext in [".htm", ".html"]:
        try:
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
            
            return {
                "type": "html",
                "content": clean_text,
                "tmp_path": None,
                "display_name": uploaded_file.name
            }
        except Exception as e:
            st.error(f"HTML処理エラー: {e}")
            return None
    
    return None
