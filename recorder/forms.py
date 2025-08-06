from django import forms

from recorder.models import Stream


class ArchivePeriodForm(forms.Form):
    start = forms.DateTimeField(
        label="Начало",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})
    )
    end = forms.DateTimeField(
        label="Конец",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})
    )


class StreamActionForm(forms.Form):
    select_across = forms.BooleanField(widget=forms.HiddenInput, required=False, label="")
    action = forms.ChoiceField(label='Действие', required=True)
    segment_duration = forms.IntegerField(label="Новая длительность (сек)", required=False, min_value=1)
    loglevel = forms.ChoiceField(
        label="Новый уровень логирования",
        required=False,
        choices=Stream.LOGLEVEL_CHOICES
    )
