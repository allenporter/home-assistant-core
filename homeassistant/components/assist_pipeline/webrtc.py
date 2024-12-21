"""Assist pipeline Websocket API."""

import asyncio

# Suppressing disable=deprecated-module is needed for Python 3.11
import dataclasses
import fractions
import io
import json
import logging
from typing import Any, Final
import uuid

from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
import av
from av.frame import Frame
from av.packet import Packet
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import DEFAULT_PIPELINE_TIMEOUT
from .error import PipelineNotFound
from .pipeline import (
    AudioSettings,
    Pipeline,
    PipelineError,
    PipelineEvent,
    PipelineEventType,
    PipelineInput,
    PipelineRun,
    PipelineStage,
    WakeWordSettings,
    async_get_pipeline,
)

_LOGGER = logging.getLogger(__name__)

CAPTURE_RATE: Final = 16000
CAPTURE_WIDTH: Final = 2
CAPTURE_CHANNELS: Final = 1
MAX_CAPTURE_TIMEOUT: Final = 60.0

SESSIONS = {}


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the websocket API."""
    websocket_api.async_register_command(hass, websocket_webrtc_offer)


PIPELINE_SCHMEA = vol.All(
    {
        # pylint: disable-next=unnecessary-lambda
        vol.Required("start_stage"): lambda val: PipelineStage(val),
        # pylint: disable-next=unnecessary-lambda
        vol.Required("end_stage"): lambda val: PipelineStage(val),
        vol.Optional("input"): dict,
        vol.Optional("pipeline"): str,
        vol.Optional("conversation_id"): vol.Any(str, None),
        vol.Optional("device_id"): vol.Any(str, None),
        vol.Optional("timeout"): vol.Any(float, int),
    },
    cv.key_value_schemas(
        "start_stage",
        {
            PipelineStage.WAKE_WORD: vol.Schema(
                {
                    vol.Required("input"): {
                        vol.Required("sample_rate"): int,
                        vol.Optional("timeout"): vol.Any(float, int),
                        vol.Optional("audio_seconds_to_buffer"): vol.Any(float, int),
                        # Audio enhancement
                        vol.Optional("noise_suppression_level"): int,
                        vol.Optional("auto_gain_dbfs"): int,
                        vol.Optional("volume_multiplier"): float,
                        # Advanced use cases/testing
                        vol.Optional("no_vad"): bool,
                    }
                },
                extra=vol.ALLOW_EXTRA,
            ),
            PipelineStage.STT: vol.Schema(
                {
                    vol.Required("input"): {
                        vol.Required("sample_rate"): int,
                        vol.Optional("wake_word_phrase"): str,
                    }
                },
                extra=vol.ALLOW_EXTRA,
            ),
            PipelineStage.INTENT: vol.Schema(
                {vol.Required("input"): {"text": str}},
                extra=vol.ALLOW_EXTRA,
            ),
            PipelineStage.TTS: vol.Schema(
                {vol.Required("input"): {"text": str}},
                extra=vol.ALLOW_EXTRA,
            ),
        },
    ),
)


@websocket_api.websocket_command(
    vol.All(
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {
                vol.Required("type"): "assist_pipeline/webrtc/offer",
                vol.Required("pipeline"): str,
                vol.Required("offer"): str,
            },
        ),
    ),
)
@websocket_api.async_response
async def websocket_webrtc_offer(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run a pipeline."""
    pipeline_id = msg.get("pipeline")
    try:
        pipeline = async_get_pipeline(hass, pipeline_id=pipeline_id)
    except PipelineNotFound:
        connection.send_error(
            msg["id"],
            "pipeline-not-found",
            f"Pipeline not found: id={pipeline_id}",
        )
        return

    session = WebRtcRealtimeSession(hass, pipeline, connection.context(msg))
    try:
        async with asyncio.timeout(10):
            answer = await session.start_session(msg["offer"])
    except TimeoutError:
        connection.send_error(msg["id"], "failed", "Timeout starting WebRTC session")
        await session.close()
        return
    except WebRtcException as e:  # XXX
        connection.send_error(msg["id"], "failed", str(e))
        await session.close()
        return
    _LOGGER.debug("Returning WebRTC answer %s", answer.sdp)
    connection.send_result(
        msg["id"],
        {
            "type": "answer",
            "answer": answer.sdp,
        },
    )
    session.async_attach()


class WebRtcException(Exception):
    """WebRTC Exception."""


class WebRtcRealtimeSession:
    """WebRTC Realtime Session."""

    def __init__(
        self, hass: HomeAssistant, pipeline: Pipeline, context: Context
    ) -> None:
        """Initialize WebRTC Realtime Session."""
        self.hass = hass
        self.pipeline = pipeline
        self.context = context
        self.pc_id = f"PeerConnection({uuid.uuid4()})"
        self.pc: RTCPeerConnection | None = None
        self.datachannel = None
        self.output_track = AudioOutputTrack()
        self.input_track = AudioInputTrack(pipeline)

    def async_attach(self) -> None:
        """Attach WebRTC session to pipeline."""
        SESSIONS[self.pc_id] = self

    def async_detach(self) -> None:
        """Remove WebRTC session from pipeline."""
        if self.pc_id in SESSIONS:
            del SESSIONS[self.pc_id]

    async def close(self) -> None:
        """Close WebRTC session."""
        await self.pc.close()
        self.async_detach()

    def _log_info(self, msg: str, *args: Any) -> None:
        """Log info."""
        _LOGGER.info(f"{self.pc_id} {msg}", *args)

    async def start_session(self, offer_sdp: str) -> Any:
        """Start a WebRTC pipeline session and return an answer."""
        self._log_info("Starting WebRTC session")

        # Create peer connection
        self.pc = RTCPeerConnection()
        self.pc.on("datachannel", self._on_datachannel)
        self.pc.on("connectionstatechange", self._on_connectionstatechange)
        self.pc.on("track", self._on_track)

        # Process the incoming offer and create an answer
        offer = RTCSessionDescription(sdp=offer_sdp, type="offer")
        await self.pc.setRemoteDescription(offer)
        self._log_info("Received offer")
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        return self.pc.localDescription

    def send_pipeline_event(self, event: PipelineEvent) -> None:
        """Send a pipeline event through the WebRTC data channel."""
        self._log_info(
            "Sending event %s to client (%s)", event.type, PipelineEventType.TTS_STREAM
        )
        if event.type == PipelineEventType.TTS_STREAM:
            # Send raw audio through the audio track rather than publishing a url

            AUDIO_PTIME = 0.020

            async def send_audio():
                audio_sample_rate = 48000
                audio_samples = 0
                audio_time_base = fractions.Fraction(1, audio_sample_rate)
                audio_resampler = av.AudioResampler(
                    format="s16",
                    layout="stereo",
                    rate=audio_sample_rate,
                    frame_size=int(audio_sample_rate * AUDIO_PTIME),
                )

                try:
                    audio_data = event.data["audio"]
                    self._log_info(
                        "Sending audio: %s bytes (%s)",
                        len(audio_data[1]),
                        audio_data[0],
                    )
                    player = MediaPlayer(io.BytesIO(audio_data[1]))
                    container = av.open(
                        file=io.BytesIO(audio_data[1]), format=audio_data[0], mode="r"
                    )
                    audio_streams = [
                        stream for stream in container.streams if stream.type == "audio"
                    ]
                    while True:
                        try:
                            frame = next(container.decode(*audio_streams))
                        except StopIteration:
                            break
                        for frame in audio_resampler.resample(frame):
                            # fix timestamps
                            frame.pts = audio_samples
                            frame.time_base = audio_time_base
                            audio_samples += frame.samples
                            self._log_info("frame.layout.name=%s", frame.layout.name)
                            await self.output_track.queue.put(frame)
                except Exception as e:
                    _LOGGER.error("Error sending audio: %s", e)
                    return

            self._log_info("Sending TTS Audio Stream")
            self.hass.create_task(send_audio())
            return
        result = websocket_api.messages.message_to_json_bytes(dataclasses.asdict(event))
        self.datachannel.send(result)

    def _on_datachannel(self, channel):
        self._log_info("Data channel %s opened", channel.label)
        self.datachannel = channel
        self.datachannel.on("message", self._on_datachannel_message)

    async def _on_datachannel_message(self, message):
        self._log_info("Received client message: %s", message)
        try:
            data = PIPELINE_SCHMEA(json.loads(message))
        except vol.Invalid as e:
            self._log_info("Invalid message: %s", e)
            self.hass.async_create_task(self.close())
            return
        self._log_info("Processing data channel command: %s", data)
        await _run_pipeline(self.hass, self, data)

    async def _on_connectionstatechange(self):
        self._log_info("Connection state is %s", self.pc.connectionState)
        if self.pc.connectionState == "failed":
            await self.pc.close()

    def _on_track(self, track):
        self._log_info("Track %s received", track.kind)
        if track.kind == "audio":
            # self.pc.addTrack(self.input_track)
            self.pc.addTrack(self.output_track)


class AudioInputTrack(MediaStreamTrack):
    """Audio Track."""

    kind = "audio"

    def __init__(self, pipeline: Pipeline) -> None:
        """Initialize AudioTrack."""
        super().__init__()
        self.pipeline = pipeline
        self.queue = asyncio.Queue()

    async def recv(self) -> Frame | Packet:
        """Receive a frame or packet."""
        _LOGGER.info("AudioTrack.recv; Ignoring packet")

        # TODO: Pipe to audio receiver


class AudioOutputTrack(MediaStreamTrack):
    """Audio Output Track."""

    kind = "audio"

    def __init__(self) -> None:
        """Initialize AudioTrack."""
        super().__init__()
        self.queue: asyncio.Queue[Frame | Packet] = asyncio.Queue()

    async def recv(self) -> Frame | Packet:
        """Receive a frame or packet."""
        _LOGGER.info("AudioOutputTrack checking queue")
        data = await self.queue.get()
        if data is None:
            self.stop()
            raise ValueError("AudioTrack stopped")
        return data


async def _run_pipeline(
    hass: HomeAssistant, session: WebRtcRealtimeSession, msg: dict[str, Any]
):
    timeout = msg.get("timeout", DEFAULT_PIPELINE_TIMEOUT)
    start_stage = PipelineStage(msg["start_stage"])
    end_stage = PipelineStage(msg["end_stage"])
    handler_id: int | None = None
    wake_word_settings: WakeWordSettings | None = None
    audio_settings: AudioSettings | None = None

    # Arguments to PipelineInput
    input_args: dict[str, Any] = {
        "conversation_id": msg.get("conversation_id"),
        "device_id": msg.get("device_id"),
    }
    if start_stage == PipelineStage.INTENT:
        # Input to conversation agent
        input_args["intent_input"] = msg["input"]["text"]
    elif start_stage == PipelineStage.TTS:
        # Input to text-to-speech system
        input_args["tts_input"] = msg["input"]["text"]
    else:
        raise ValueError(f"Invalid start stage: {start_stage}")

    input_args["run"] = PipelineRun(
        hass,
        context=session.context,
        pipeline=session.pipeline,
        start_stage=start_stage,
        end_stage=end_stage,
        event_callback=session.send_pipeline_event,
        runner_data={
            "stt_binary_handler_id": handler_id,
            "timeout": timeout,
        },
        wake_word_settings=wake_word_settings,
        audio_settings=audio_settings or AudioSettings(),
    )

    pipeline_input = PipelineInput(**input_args)

    try:
        await pipeline_input.validate()
    except PipelineError as error:
        # Report more specific error when possible
        session.send_pipeline_event(msg["id"], error.code, error.message)
        return

    run_task = hass.async_create_task(pipeline_input.execute())

    try:
        # Task contains a timeout
        async with asyncio.timeout(timeout):
            await run_task
    except TimeoutError:
        pipeline_input.run.process_event(
            PipelineEvent(
                PipelineEventType.ERROR,
                {"code": "timeout", "message": "Timeout running pipeline"},
            )
        )
