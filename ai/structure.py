from pydantic import BaseModel, Field, field_validator
import re

class Structure(BaseModel):
    tldr: str = Field(description="generate a too long; didn't read summary")
    motivation: str = Field(description="describe the motivation in this paper")
    method: str = Field(description="method of this paper")
    result: str = Field(description="result of this paper")
    conclusion: str = Field(description="conclusion of this paper")

def create_structure(language: str):
    """Create a Structure model with language-specific field descriptions."""
    lang_hint = f" (respond in {language})"

    class LocalizedStructure(BaseModel):
        tldr: str = Field(description="generate a too long; didn't read summary" + lang_hint)
        motivation: str = Field(description="describe the motivation in this paper" + lang_hint)
        method: str = Field(description="method of this paper" + lang_hint)
        result: str = Field(description="result of this paper" + lang_hint)
        conclusion: str = Field(description="conclusion of this paper" + lang_hint)

    # The class name must be "Structure" so that the function calling schema
    # name matches what the error handling in enhance.py expects.
    LocalizedStructure.__name__ = "Structure"
    LocalizedStructure.__qualname__ = "Structure"
    return LocalizedStructure