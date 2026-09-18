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

## Scheduled prompts (queue)

Every prompt you send is placed on a schedule. Prompts are generated **one after another, in order**: as soon as the first image finishes, the next prompt starts automatically. You can keep typing and sending prompts while images are generating — they line up in the queue.

To schedule many prompts at once, use the **Schedule prompts** box in the sidebar:

1. Write one prompt per line (as many as you want — 20, 50, more).
2. Press **＋ Add to schedule**. Every line becomes one queued image.
3. The worker generates them one by one, in order, without overlapping.

- Live progress (current prompt + how many are waiting) is shown above the chat input and in the sidebar under **Scheduled prompts**.
- Each scheduled image is saved to the conversation that was active when you added the prompt — switching conversations mid-queue does not move later images.
- Remove a single prompt with the **×** button next to it, or empty the whole schedule with **Clear schedule**.

## Notes

- Image generation uses `gpt-image-1` by default.
- The model and output size can be changed in the right-side controls.
- Do not commit `image_chat.db`, generated images, or API keys.
