import os
import logging
import threading
import srt
from PySubtitleGPT import SubtitleBatch
from PySubtitleGPT import SubtitleError
from PySubtitleGPT.Helpers import GenerateTag, GetInputPath, GetOutputPath, ParseCharacters, ParseSubstitutions, UnbatchScenes
from PySubtitleGPT.SubtitleScene import SubtitleScene
from PySubtitleGPT.SubtitleLine import SubtitleLine
from PySubtitleGPT.SubtitleBatcher import SubtitleBatcher
from PySubtitleGPT.SubtitleError import TranslationError

default_encoding = os.getenv('DEFAULT_ENCODING', 'utf-8')
fallback_encoding = os.getenv('DEFAULT_ENCODING', 'iso-8859-1')

class SubtitleFile:
    """
    High level class for manipulating subtitle files
    """
    def __init__(self, filepath = None, outputpath = None):
        self.originals : list[SubtitleLine] = None
        self.translated : list[SubtitleLine] = None
        self.context = {}
        self._scenes : list[SubtitleScene] = []
        self.lock = threading.RLock()

        self.sourcepath = GetInputPath(filepath)
        self.outputpath = outputpath or None

    @property
    def has_subtitles(self):
        return self.linecount > 0 or self.scenecount > 0
    
    @property
    def linecount(self):
        with self.lock:
            return len(self.originals) if self.originals else 0
    
    @property
    def scenecount(self):
        with self.lock:
            return len(self.scenes) if self.scenes else 0
    
    @property
    def scenes(self):
        return self._scenes
    
    @scenes.setter
    def scenes(self, scenes : list[SubtitleScene]):
        with self.lock:
            self._scenes = scenes
            self.originals, self.translated, _ = UnbatchScenes(scenes)
            self.Renumber()

    def GetScene(self, scene_number : int) -> SubtitleScene:
        if not self.scenes:
            raise SubtitleError("Subtitles have not been batched")
        
        with self.lock:
            matches = [scene for scene in self.scenes if scene.number == scene_number ]
    
        if not matches:
            raise SubtitleError(f"Scene {scene_number} does not exist")
        
        if len(matches) > 1:
            raise SubtitleError(f"There is more than one scene {scene_number}!")
        
        return matches[0]
    
    def GetBatch(self, scene_number : int, batch_number : int) -> SubtitleBatch:
        scene = self.GetScene(scene_number)
        for batch in scene.batches:
            if batch.number == batch_number:
                return batch
        
        raise SubtitleError(f"Scene {scene_number} batch {batch_number} doesn't exist")
    

    def GetBatchContext(self, scene_number : int, batch_number : int, max_lines : int = None) -> list[str]:
        """
        Get context for a batch of subtitles, by extracting summaries from previous scenes and batches
        """
        context_lines = []
        last_summary = ""
        for scene in self.scenes:
            if scene.number == scene_number:
                break

            if scene.summary and scene.summary != last_summary and scene.summary != "New scene":
                context_lines.append(f"scene {scene.number}: {scene.summary}")
                last_summary = scene.summary

        if not scene:
            raise SubtitleError(f"Failed to find scene {scene_number}")

        for batch in scene.batches:
            if batch.number == batch_number:
                break

            if batch.summary and batch.summary != last_summary and batch.summary != "New scene":
                context_lines.append(f"scene {batch.scene} batch {batch.number}: {batch.summary}")
                last_summary = batch.summary

        if max_lines:
            context_lines = context_lines[-max_lines:]

        return context_lines


    def LoadSubtitles(self, filepath : str = None):
        """
        Load subtitles from an SRT file
        """
        if filepath:
            self.sourcepath = GetInputPath(filepath)
            self.outputpath = GetOutputPath(filepath)

        try:
            with open(self.sourcepath, 'r', encoding=default_encoding) as f:
                source = list(srt.parse(f))
            
        except srt.SRTParseError as e:
            with open(self.sourcepath, 'r', encoding=fallback_encoding) as f:
                source = list(srt.parse(f))

        with self.lock:
            self.originals = [ SubtitleLine(item) for item in source ]

    def SaveOriginals(self, path : str = None):
        """
        Write original subtitles to an SRT file
        """
        self.sourcepath = path or self.sourcepath
        if not self.sourcepath:
            raise ValueError("No file path set")

        with self.lock:
            srtfile = srt.compose([ line.item for line in self.originals ])
            with open(self.sourcepath, 'w', encoding=default_encoding) as f:
                f.write(srtfile)

    def SaveTranslation(self, outputpath : str = None):
        """
        Write translated subtitles to an SRT file
        """
        outputpath = outputpath or self.outputpath 
        if not outputpath:
            outputpath = GetOutputPath(self.sourcepath)
            if not outputpath:
                raise Exception("I don't know where to save the translated subtitles")

        if not self.scenes:
            raise ValueError("No scenes in subtitles")

        with self.lock:
            # Linearise the translated scenes
            originals, translated, untranslated = UnbatchScenes(self.scenes)

            if not translated:
                logging.error("No subtitles translated")
                return

            logging.info(f"Saving translation to {str(outputpath)}")

            srtfile = srt.compose([ line.item for line in translated ])
            with open(outputpath, 'w', encoding=default_encoding) as f:
                f.write(srtfile)

            self.translated = translated
            self.outputpath = outputpath

    def UpdateContext(self, options):
        """
        Update the project context from options,
        and set any unspecified options from the project context.
        """
        if hasattr(options, 'options'):
            return self.UpdateContext(options.options)
    
        context = {
            'gpt_model': "",
            'gpt_prompt': "",
            'instructions': "",
            'movie_name': "",
            'synopsis': "",
            'characters': None,
            'substitutions': None,
            'min_batch_size' : None,
            'max_batch_size' : None,
            'batch_threshold' : None,
            'scene_threshold' : None,
        }

        with self.lock:
            if self.context:
                context = {**context, **self.context}

            context['characters'] = ParseCharacters(context.get('characters'))
            options['characters'] = ParseCharacters(options.get('characters'))

            context['substitutions'] = ParseSubstitutions(context.get('substitutions'))
            options['substitutions'] = ParseSubstitutions(options.get('substitutions'))

            # Update the context dictionary with matching fields from options, and vice versa
            for key in context.keys():
                if options.get(key):
                    context[key] = options[key]
                elif context[key]:
                    options[key] = context[key]

            self.context = context

        return context

    def AutoBatch(self, options):
        """
        Divide subtitles into scenes and batches based on threshold options
        """
        batcher = SubtitleBatcher(options)

        with self.lock:
            self.scenes = batcher.BatchSubtitles(self.originals)

    def AddScene(self, scene):
        with self.lock:
            self.scenes.append(scene)
            logging.debug("Added a new scene")

    def MergeScenes(self, scene_numbers: list[int]):
        """
        Merge several (sequential) scenes into one scene 
        """
        if not scene_numbers:
            raise ValueError("No scene numbers supplied to MergeScenes")

        scene_numbers = sorted(scene_numbers)
        if scene_numbers != list(range(scene_numbers[0], scene_numbers[0] + len(scene_numbers))):
            raise ValueError("Scene numbers to be merged are not sequential")

        with self.lock:
            scenes = [scene for scene in self.scenes if scene.number in scene_numbers]
            if len(scenes) != len(scene_numbers):
                raise ValueError(f"Could not find scenes {','.join(scene_numbers)}")

            # Merge all scenes into the first
            scenes[0].MergeScenes(scenes[1:])

            # Slice out the merged scenes
            start_index = self.scenes.index(scenes[0])
            end_index = self.scenes.index(scenes[-1])
            self.scenes = self.scenes[:start_index + 1] + self.scenes[end_index+1:]

            for number, scene in enumerate(self.scenes, start = 1):
                scene.number = number

    def MergeBatches(self, scene_number : int, batch_numbers: list[int]):
        """
        Merge several (sequential) batches from a scene into one batch 
        """
        if not batch_numbers:
            raise ValueError("No batch numbers supplied to MergeBatches")

        with self.lock:
            scene : SubtitleScene = next((scene for scene in self.scenes if scene.number == scene_number), None)
            if not scene:
                raise ValueError(f"Scene {str(scene_number)} not found")

            scene.MergeBatches(batch_numbers)

    def MergeLines(self, hierarchy : dict):
        """
        Merge several sequential lines together, remapping originals and translated lines if necessary.
        """
        with self.lock:
            for scene_number in hierarchy.keys():
                for batch_number in hierarchy[scene_number].keys():
                    batch_dict = hierarchy[scene_number][batch_number]
                    original_lines = list(batch_dict['originals'].keys())
                    translated_lines = list(batch_dict['translated'].keys())

                    batch : SubtitleBatch = self.GetBatch(scene_number, batch_number)
                    batch.MergeLines(original_lines, translated_lines)

    def Renumber(self):
        """
        Force monotonic numbering of scenes, batches, lines and translated lines
        """
        with self.lock:
            for scene_number, scene in enumerate(self.scenes, start=1):
                scene.number = scene_number
                for batch_number, batch in enumerate(scene.batches, start=1):
                    batch.number = batch_number
                    batch.scene = scene.number

            # Renumber lines sequentially and remap translated indexes
            translated_map = { translated.number: translated for translated in self.translated } if self.translated else None

            for number, line in enumerate(self.originals, start=1):
                # If there is a matching translation, remap its number
                if translated_map and line.number in translated_map:
                    translated = translated_map[line.number]
                    translated.number = number
                    del translated_map[line.number]

                line.number = number
