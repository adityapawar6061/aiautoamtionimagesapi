# Canvas Chat

A Streamlit image-generation chat powered by the OpenAI Images API. It stores conversations, messages, image metadata, and generated image files locally.

## Run

1. Create and activate a virtual environment.
2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the app:

   ```powershell
   streamlit run app.py
   ```

4. Add your OpenAI API key in the sidebar.

The API key is held only in the current Streamlit session. Conversation data is stored in `image_chat.db`, and generated PNG files are stored in `generated_images/`.

## Notes

- Image generation uses `gpt-image-1` by default.
- The model and output size can be changed in the right-side controls.
- Do not commit `image_chat.db`, generated images, or API keys.
