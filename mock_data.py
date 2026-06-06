"""
Mock search results and article text for the brain simulation test question.
Swap SEARCH_RESULTS and ARTICLE_TEXT for real API calls (Tavily, SerpAPI, Jina)
to make the system live.
"""
from contracts import Source

# Mock search results for two different queries
SEARCH_RESULTS: dict[str, list[Source]] = {
    "brain simulation approaches computer": [
        Source(
            url="https://www.humanbrainproject.eu/en/science-development/focus-areas/brain-simulation/",
            title="Human Brain Project: Brain Simulation",
            snippet="The Human Brain Project has developed tools for multi-scale brain simulation, including the EBRAINS platform which supports simulation of neural circuits at multiple levels of detail.",
            relevance_score=0.95,
        ),
        Source(
            url="https://www.intel.com/content/www/us/en/research/neuromorphic-computing.html",
            title="Intel Loihi 2: Neuromorphic Computing Chip",
            snippet="Intel's Loihi 2 neuromorphic chip implements spiking neural networks in silicon, achieving brain-like computation with 1000x better energy efficiency than traditional processors for certain workloads.",
            relevance_score=0.88,
        ),
        Source(
            url="https://apt.cs.manchester.ac.uk/projects/SpiNNaker/",
            title="SpiNNaker: Spiking Neural Network Architecture",
            snippet="SpiNNaker (Spiking Neural Network Architecture) at University of Manchester can simulate up to 1 billion simple neurons in real time, representing one of the largest neuromorphic computing systems.",
            relevance_score=0.85,
        ),
    ],
    "latest milestones whole brain emulation 2024 2025": [
        Source(
            url="https://www.science.org/doi/10.1126/science.brain.simulation.2024",
            title="Blue Brain Project: Detailed Simulation of Mouse Cortical Column",
            snippet="The Blue Brain Project published a detailed simulation of a mouse cortical column containing 31,000 neurons and 37 million synapses, validating emergent properties against experimental recordings.",
            relevance_score=0.92,
        ),
        Source(
            url="https://alleninstitute.org/division/brain-science/openscope/",
            title="Allen Institute: Whole Brain Activity Mapping",
            snippet="The Allen Institute's OpenScope program has mapped activity across the entire mouse brain during behavior, providing ground truth data for validating large-scale neural simulations.",
            relevance_score=0.80,
        ),
    ],
}

# Mock article text for each URL — returned by the fetch_page tool
ARTICLE_TEXT: dict[str, str] = {
    "https://www.humanbrainproject.eu/en/science-development/focus-areas/brain-simulation/": """
    The Human Brain Project (HBP) represents Europe's largest neuroscience initiative, running from 2013 to 2023
    with €600 million in funding. The project's simulation pillar developed the EBRAINS platform, which provides
    open access to brain atlases, simulation tools, and neural data.

    Key achievements include: multi-scale simulation frameworks that bridge molecular, cellular, and circuit-level
    models; the PyNN interface for hardware-agnostic neural simulation; and collaborations with neuromorphic
    hardware teams at Intel and IBM.

    Critics have noted that the HBP's original goal of simulating the entire human brain by 2023 was not achieved,
    and the project pivoted significantly toward data infrastructure. Henry Markram, the project's founder,
    has argued that a bottom-up simulation approach (building from individual neurons) is the correct path,
    while others argue for functional/behavioral approaches.
    """,
    "https://www.intel.com/content/www/us/en/research/neuromorphic-computing.html": """
    Intel's Loihi 2 neuromorphic research chip (2021) contains 1 million programmable neurons and 120 million
    synapses. Unlike traditional von Neumann architectures, Loihi 2 uses asynchronous spiking communication
    that more closely resembles biological neural signaling.

    Benchmark results show 1000x energy efficiency gains over GPU implementations for sparse, event-driven
    workloads like keyword detection and robotic control. The chip is not designed for general brain simulation
    but for specific neuromorphic computing tasks.

    Intel's approach is explicitly not aimed at whole-brain emulation — it is focused on brain-inspired
    computing architectures that extract computational principles from neuroscience without attempting to
    replicate biological detail.
    """,
    "https://apt.cs.manchester.ac.uk/projects/SpiNNaker/": """
    SpiNNaker (Spiking Neural Network Architecture) is a massively parallel computing platform developed at
    the University of Manchester. The full SpiNNaker machine contains 1 million ARM processor cores connected
    by a custom communication fabric.

    The system can simulate approximately 1 billion simple spiking neurons in real time — roughly 1% of a
    human brain's neuron count. A key use case is simulating neural circuits to test computational models
    of brain function.

    SpiNNaker2, currently in development, targets 10 billion neurons through improved chip architecture.
    The platform has been integrated into the Human Brain Project's EBRAINS ecosystem.
    """,
    "https://www.science.org/doi/10.1126/science.brain.simulation.2024": """
    The Blue Brain Project (BBP), led by Henry Markram at EPFL, published in 2024 their most detailed
    simulation to date: a complete model of a mouse somatosensory cortical column.

    The simulation contains 31,000 morphologically detailed neurons and 37 million synapses, reconstructed
    from electron microscopy data. When run, the simulation reproduces spontaneous activity patterns,
    response properties, and oscillatory dynamics that match experimental recordings.

    This represents a landmark in simulation fidelity — previous models used simplified neuron models.
    The full simulation requires approximately 1,000 CPU cores running for several hours to simulate
    one second of biological time.

    The BBP team argues this approach proves that detailed biophysical simulation can reproduce emergent
    brain dynamics, supporting the hypothesis that sufficient computational detail will scale to full
    brain simulation. Scaling to a full mouse brain (71 million neurons) would require roughly 100x
    more compute resources.
    """,
    "https://alleninstitute.org/division/brain-science/openscope/": """
    The Allen Institute for Brain Science's OpenScope program provides open access to large-scale neural
    recording data from behaving mice. Using two-photon calcium imaging and Neuropixels probes, the program
    records from thousands of neurons simultaneously across multiple brain regions.

    This data serves as ground truth for validating simulation models — a simulation can be considered
    accurate if it reproduces the statistical properties of the Allen Institute's recordings.

    Recent OpenScope datasets include recordings of visual cortex activity during movie viewing (150,000 neurons),
    and whole-brain activity maps showing which regions co-activate during specific behaviors. These datasets
    are freely available and have become standard benchmarks for simulation validation.
    """,
}


def mock_search(query: str) -> list[Source]:
    """Return mock sources for a query. Fuzzy-matches against known queries."""
    query_lower = query.lower()
    for key, sources in SEARCH_RESULTS.items():
        if any(word in query_lower for word in key.split()):
            return sources
    # Default: return all sources if no match
    return SEARCH_RESULTS["brain simulation approaches computer"]


def mock_fetch(url: str) -> str:
    """Return mock article text for a URL."""
    return ARTICLE_TEXT.get(url, f"[No mock content available for {url}]")
