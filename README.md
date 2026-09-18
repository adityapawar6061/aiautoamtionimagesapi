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

Images are generated on a dedicated background thread, so prompts are always generated **strictly one after another, in order** — and nothing you do while an image is generating (adding more prompts, adding a schedule, switching conversations) can interrupt or restart it.

**Schedule several at once:** press **＋ Add to schedule** in the sidebar, then:

1. Choose **how many images** you want to schedule.
2. Fill in one prompt per field (that many fields appear).
3. Press **＋ Add all to schedule** — they all join the queue and generate one by one.

You can also queue a prompt any time from the normal chat input — it just joins the end of the queue.

- Live progress (current prompt + waiting list) is shown above the chat input and in the sidebar.
- Each image is saved to the conversation that was active when you scheduled it — switching conversations mid-queue does not move later images.
- Remove a single waiting prompt with the **×** button next to it, or empty the queue with **Clear schedule**.

## Notes

- Image generation uses `gpt-image-1` by default.
- The model and output size can be changed in the right-side controls.
- Do not commit `image_chat.db`, generated images, or API keys.
