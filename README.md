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

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. Open [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Select the repository, choose the `main` branch, and set the main file to `app.py`.
4. Click **Deploy**.
5. Open the deployed app and enter your OpenAI API key in the sidebar.

The deployed app is ready to run without a secrets file because the key is entered in the UI. Streamlit Community Cloud has ephemeral local storage, so SQLite history and generated images are not guaranteed to survive app restarts or redeployments. Use a hosted database and object storage for permanent production history.

## Notes

- Image generation uses `gpt-image-1` by default.
- The model and output size can be changed in the right-side controls.
- Do not commit `image_chat.db`, generated images, or API keys.
