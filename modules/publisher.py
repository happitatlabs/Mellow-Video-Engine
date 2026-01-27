"""
Publisher Module
================
State 5: Localization & Deployment
Handles multi-language translation and YouTube upload preparation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from core.project_state import ProjectState
    from core.model_manager import ModelManager
    from core.fsm_manager import FSMManager

from core.fsm_manager import StateHandler
from core.model_manager import ModelType, ModelContext

logger = logging.getLogger(__name__)


@dataclass
class LocalizedContent:
    """Localized content for a single language."""
    language: str
    title: str
    description: str
    tags: list[str]
    lyrics: Optional[str] = None


@dataclass
class YouTubeMetadata:
    """YouTube video metadata for upload."""
    title: str
    description: str
    tags: list[str]
    category_id: str = "10"  # Music category
    privacy_status: str = "private"  # private, public, unlisted
    made_for_kids: bool = False
    default_language: str = "ko"
    localizations: dict[str, dict] = None  # language -> {title, description}


class Translator:
    """
    Multi-language translator using local LLM.

    Features:
    - Batch translation for efficiency
    - Music industry terminology awareness
    - SEO-friendly translations
    """

    # Language name mapping
    LANGUAGE_NAMES = {
        "ko": "Korean",
        "en": "English",
        "ja": "Japanese",
        "zh-CN": "Simplified Chinese",
        "zh-TW": "Traditional Chinese",
    }

    def __init__(
        self,
        model_manager: ModelManager,
        llm_config: dict,
        prompts_config: dict,
    ):
        """
        Initialize Translator.

        Args:
            model_manager: ModelManager for VRAM management
            llm_config: LLM configuration
            prompts_config: Prompt templates
        """
        self.model_manager = model_manager
        self.llm_config = llm_config
        self.prompts_config = prompts_config
        self.logger = logging.getLogger(self.__class__.__name__)

    async def translate_metadata(
        self,
        title: str,
        description: str,
        tags: list[str],
        source_lang: str,
        target_langs: list[str],
    ) -> dict[str, LocalizedContent]:
        """
        Translate metadata to multiple languages.

        Args:
            title: Original title
            description: Original description
            tags: Original tags
            source_lang: Source language code
            target_langs: Target language codes

        Returns:
            Dictionary mapping language code to LocalizedContent
        """
        translations = {}

        # Add source language
        translations[source_lang] = LocalizedContent(
            language=source_lang,
            title=title,
            description=description,
            tags=tags,
        )

        # Load LLM
        async with ModelContext(
            self.model_manager,
            ModelType.LLM,
            self.llm_config,
            auto_unload=False,
        ) as llm_client:
            system_prompt = self.prompts_config.get(
                "translation", {}
            ).get("system_prompt", "")

            for target_lang in target_langs:
                if target_lang == source_lang:
                    continue

                self.logger.info(
                    f"Translating to {self.LANGUAGE_NAMES.get(target_lang, target_lang)}"
                )

                try:
                    # Build translation prompt
                    user_prompt = f"""
Translate the following YouTube video metadata from {self.LANGUAGE_NAMES.get(source_lang, source_lang)} to {self.LANGUAGE_NAMES.get(target_lang, target_lang)}.

TITLE: {title}

DESCRIPTION:
{description}

TAGS: {', '.join(tags)}

Output in this exact JSON format:
{{
    "title": "translated title",
    "description": "translated description",
    "tags": ["tag1", "tag2", "tag3"]
}}

Important:
- Keep artist names and song titles in their original form
- Use natural, native expressions
- Maintain SEO keywords
- Keep the emotional tone
"""

                    response = await self._call_llm(
                        llm_client,
                        system_prompt,
                        user_prompt,
                    )

                    # Parse JSON response
                    try:
                        # Find JSON in response
                        json_start = response.find("{")
                        json_end = response.rfind("}") + 1
                        if json_start >= 0 and json_end > json_start:
                            json_str = response[json_start:json_end]
                            data = json.loads(json_str)

                            translations[target_lang] = LocalizedContent(
                                language=target_lang,
                                title=data.get("title", title),
                                description=data.get("description", description),
                                tags=data.get("tags", tags),
                            )
                        else:
                            raise ValueError("No JSON found in response")

                    except json.JSONDecodeError as e:
                        self.logger.warning(
                            f"Failed to parse translation for {target_lang}: {e}"
                        )
                        # Fallback to original
                        translations[target_lang] = LocalizedContent(
                            language=target_lang,
                            title=title,
                            description=description,
                            tags=tags,
                        )

                except Exception as e:
                    self.logger.error(f"Translation failed for {target_lang}: {e}")
                    translations[target_lang] = LocalizedContent(
                        language=target_lang,
                        title=title,
                        description=description,
                        tags=tags,
                    )

        return translations

    async def translate_lyrics(
        self,
        lyrics: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """
        Translate song lyrics.

        Args:
            lyrics: Original lyrics
            source_lang: Source language code
            target_lang: Target language code

        Returns:
            Translated lyrics
        """
        if source_lang == target_lang:
            return lyrics

        async with ModelContext(
            self.model_manager,
            ModelType.LLM,
            self.llm_config,
            auto_unload=True,
        ) as llm_client:
            system_prompt = self.prompts_config.get(
                "translation", {}
            ).get("system_prompt", "")

            lyrics_prompt = self.prompts_config.get(
                "translation", {}
            ).get("lyrics_template", "").format(
                target_language=self.LANGUAGE_NAMES.get(target_lang, target_lang),
                lyrics=lyrics,
            )

            if not lyrics_prompt:
                lyrics_prompt = f"""
Translate these song lyrics to {self.LANGUAGE_NAMES.get(target_lang, target_lang)}:

{lyrics}

Maintain the poetic feel and emotional impact.
Keep line breaks aligned with the original.
Output only the translated lyrics, nothing else.
"""

            response = await self._call_llm(llm_client, system_prompt, lyrics_prompt)
            return response.strip()

    async def _call_llm(
        self,
        llm_client: dict,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Call LLM for translation."""
        if llm_client.get("type") == "ollama":
            client = llm_client.get("client")
            model = llm_client.get("model_name")

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
            }

            response = await client.post("/api/chat", json=payload)

            if response.status_code == 200:
                data = response.json()
                return data.get("message", {}).get("content", "")
            else:
                raise RuntimeError(f"LLM call failed: {response.status_code}")

        return ""


class YouTubeUploader:
    """
    YouTube Data API v3 integration for video upload.

    Features:
    - OAuth2 authentication
    - Resumable uploads
    - Localization support
    - Scheduling
    """

    def __init__(
        self,
        client_secrets_file: Path,
        credentials_file: Path,
        scopes: list[str] = None,
    ):
        """
        Initialize YouTubeUploader.

        Args:
            client_secrets_file: Path to client_secrets.json
            credentials_file: Path to store credentials
            scopes: OAuth2 scopes
        """
        self.client_secrets_file = Path(client_secrets_file)
        self.credentials_file = Path(credentials_file)
        self.scopes = scopes or [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
        ]
        self._service = None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def authenticate(self) -> bool:
        """
        Authenticate with YouTube API.

        Returns:
            True if authentication successful
        """
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            credentials = None

            # Load existing credentials
            if self.credentials_file.exists():
                credentials = Credentials.from_authorized_user_file(
                    str(self.credentials_file),
                    self.scopes,
                )

            # Refresh or get new credentials
            if not credentials or not credentials.valid:
                if credentials and credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                else:
                    if not self.client_secrets_file.exists():
                        self.logger.error(
                            f"Client secrets file not found: {self.client_secrets_file}"
                        )
                        return False

                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.client_secrets_file),
                        self.scopes,
                    )
                    credentials = flow.run_local_server(port=0)

                # Save credentials
                self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.credentials_file, "w") as f:
                    f.write(credentials.to_json())

            # Build service
            self._service = build("youtube", "v3", credentials=credentials)
            self.logger.info("YouTube API authentication successful")
            return True

        except ImportError:
            self.logger.error(
                "Google API packages not installed. Run: "
                "pip install google-auth-oauthlib google-api-python-client"
            )
            return False
        except Exception as e:
            self.logger.error(f"YouTube authentication failed: {e}")
            return False

    async def upload_video(
        self,
        video_path: Path,
        metadata: YouTubeMetadata,
        progress_callback: Optional[callable] = None,
    ) -> Optional[str]:
        """
        Upload video to YouTube.

        Args:
            video_path: Path to video file
            metadata: Video metadata
            progress_callback: Upload progress callback

        Returns:
            Video ID if successful, None otherwise
        """
        if not self._service:
            if not await self.authenticate():
                return None

        try:
            from googleapiclient.http import MediaFileUpload

            # Build video resource
            body = {
                "snippet": {
                    "title": metadata.title,
                    "description": metadata.description,
                    "tags": metadata.tags,
                    "categoryId": metadata.category_id,
                    "defaultLanguage": metadata.default_language,
                },
                "status": {
                    "privacyStatus": metadata.privacy_status,
                    "madeForKids": metadata.made_for_kids,
                    "selfDeclaredMadeForKids": metadata.made_for_kids,
                },
            }

            # Add localizations if available
            if metadata.localizations:
                body["localizations"] = {}
                for lang, loc_data in metadata.localizations.items():
                    body["localizations"][lang] = {
                        "title": loc_data.get("title", metadata.title),
                        "description": loc_data.get("description", metadata.description),
                    }

            # Create media upload
            media = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                resumable=True,
                chunksize=1024 * 1024 * 10,  # 10MB chunks
            )

            # Execute upload
            request = self._service.videos().insert(
                part="snippet,status,localizations",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and progress_callback:
                    await progress_callback(status.progress() * 100)

            video_id = response.get("id")
            self.logger.info(f"Video uploaded successfully: {video_id}")
            return video_id

        except Exception as e:
            self.logger.error(f"Video upload failed: {e}")
            return None

    def prepare_metadata(
        self,
        project_metadata: dict,
        translations: dict[str, LocalizedContent],
        source_lang: str = "ko",
    ) -> YouTubeMetadata:
        """
        Prepare YouTube metadata from project data and translations.

        Args:
            project_metadata: Project metadata dict
            translations: Localized content dictionary
            source_lang: Source language code

        Returns:
            YouTubeMetadata object
        """
        source_content = translations.get(source_lang)

        # Build description
        description_parts = [
            project_metadata.get("song_title", ""),
            "",
            f"Artist: {project_metadata.get('artist', '')}",
            f"Lyrics: {project_metadata.get('lyricist', '')}",
            f"Composed by: {project_metadata.get('composer', '')}",
            "",
            "---",
            "",
            project_metadata.get("story_description", ""),
            "",
            "#music #musicvideo #AI",
        ]
        description = "\n".join(description_parts)

        # Build localizations
        localizations = {}
        for lang, content in translations.items():
            if lang != source_lang:
                localizations[lang] = {
                    "title": content.title,
                    "description": content.description,
                }

        return YouTubeMetadata(
            title=source_content.title if source_content else project_metadata.get("title", ""),
            description=description,
            tags=source_content.tags if source_content else [],
            default_language=source_lang,
            localizations=localizations,
        )


class Publisher:
    """
    High-level publisher for localization and deployment.
    """

    def __init__(
        self,
        model_manager: ModelManager,
        llm_config: dict,
        prompts_config: dict,
        youtube_config: dict,
        localization_config: dict,
    ):
        """
        Initialize Publisher.

        Args:
            model_manager: ModelManager instance
            llm_config: LLM configuration
            prompts_config: Prompt templates
            youtube_config: YouTube API configuration
            localization_config: Localization settings
        """
        self.translator = Translator(model_manager, llm_config, prompts_config)
        self.youtube = YouTubeUploader(
            client_secrets_file=Path(youtube_config.get("client_secrets_file", "")),
            credentials_file=Path(youtube_config.get("credentials_file", "")),
            scopes=youtube_config.get("scopes", []),
        )
        self.localization_config = localization_config
        self.logger = logging.getLogger(self.__class__.__name__)

    async def localize_content(
        self,
        project: ProjectState,
    ) -> dict[str, LocalizedContent]:
        """
        Generate localized content for all target languages.

        Args:
            project: Project state

        Returns:
            Dictionary of localized content
        """
        source_lang = self.localization_config.get("source_language", "ko")
        target_langs = self.localization_config.get("target_languages", ["en", "ja", "zh-CN"])

        # Build source content
        title = f"{project.metadata.artist} - {project.metadata.song_title}"
        description = project.metadata.story_description
        tags = ["music", "musicvideo", "AI", project.metadata.artist]

        # Translate
        translations = await self.translator.translate_metadata(
            title=title,
            description=description,
            tags=tags,
            source_lang=source_lang,
            target_langs=target_langs,
        )

        # Store in project
        for lang, content in translations.items():
            project.metadata.translations[lang] = {
                "title": content.title,
                "description": content.description,
                "tags": content.tags,
            }

        return translations

    async def prepare_for_upload(
        self,
        project: ProjectState,
        translations: dict[str, LocalizedContent],
    ) -> YouTubeMetadata:
        """
        Prepare YouTube metadata for upload.

        Args:
            project: Project state
            translations: Localized content

        Returns:
            YouTubeMetadata object
        """
        return self.youtube.prepare_metadata(
            project_metadata=project.metadata.to_dict(),
            translations=translations,
            source_lang=self.localization_config.get("source_language", "ko"),
        )

    async def upload_to_youtube(
        self,
        video_path: Path,
        metadata: YouTubeMetadata,
        progress_callback: Optional[callable] = None,
    ) -> Optional[str]:
        """
        Upload video to YouTube.

        Args:
            video_path: Path to video file
            metadata: YouTube metadata
            progress_callback: Progress callback

        Returns:
            Video ID if successful
        """
        return await self.youtube.upload_video(
            video_path=video_path,
            metadata=metadata,
            progress_callback=progress_callback,
        )


# ============================================================================
# FSM Handler
# ============================================================================

class LocalizationHandler(StateHandler):
    """FSM Handler for LOCALIZATION state."""

    def __init__(
        self,
        fsm: FSMManager,
        model_manager: ModelManager,
        llm_config: dict,
        prompts_config: dict,
        youtube_config: dict,
        localization_config: dict,
    ):
        super().__init__(fsm)
        self.model_manager = model_manager
        self.llm_config = llm_config
        self.prompts_config = prompts_config
        self.youtube_config = youtube_config
        self.localization_config = localization_config
        self.publisher: Optional[Publisher] = None

    async def enter(self, project: ProjectState) -> None:
        """Initialize publisher."""
        self.logger.info("Entering LOCALIZATION state")

        self.publisher = Publisher(
            model_manager=self.model_manager,
            llm_config=self.llm_config,
            prompts_config=self.prompts_config,
            youtube_config=self.youtube_config,
            localization_config=self.localization_config,
        )

    async def execute(self, project: ProjectState) -> tuple[bool, str]:
        """Execute localization."""
        try:
            self.logger.info("Starting content localization...")

            # Generate translations
            translations = await self.publisher.localize_content(project)

            self.logger.info(
                f"Localization complete for {len(translations)} languages"
            )

            # Prepare YouTube metadata
            youtube_metadata = await self.publisher.prepare_for_upload(
                project=project,
                translations=translations,
            )

            # Store metadata for deployment stage
            project.metadata.translations["youtube_metadata"] = {
                "title": youtube_metadata.title,
                "description": youtube_metadata.description,
                "tags": youtube_metadata.tags,
                "localizations": youtube_metadata.localizations,
            }

            return True, "localization_complete"

        except Exception as e:
            self.logger.exception(f"Localization failed: {e}")
            return False, "localization_failed"

    async def exit(self, project: ProjectState) -> None:
        """Cleanup."""
        self.logger.info("Exiting LOCALIZATION state")

        # Unload LLM
        if self.model_manager.is_model_loaded(ModelType.LLM):
            await self.model_manager.unload_model(ModelType.LLM)

        self.publisher = None
